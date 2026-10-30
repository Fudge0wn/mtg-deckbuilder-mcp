# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=2.3,<3", "httpx"]
# ///
"""MCP server for building Magic: The Gathering decks with Scryfall data."""

import re
import threading
import time

import httpx
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("scryfall")

api = httpx.Client(
    base_url="https://api.scryfall.com",
    headers={"User-Agent": "scryfall-mcp/0.1", "Accept": "application/json"},
    timeout=30,
)

# Scryfall asks for 50-100 ms between requests.
_MIN_INTERVAL = 0.1
_last_request = 0.0
_lock = threading.Lock()

# Card data changes at most daily; cache responses for 24 h.
_CACHE_TTL = 24 * 3600
_cache: dict[str, tuple[float, httpx.Response]] = {}


def _request(method: str, path: str, **kwargs) -> httpx.Response:
    global _last_request
    key = f"{method} {path} {kwargs.get('params')} {kwargs.get('json')}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    with _lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        r = api.request(method, path, **kwargs)
        _last_request = time.monotonic()
    if r.status_code == 429:
        raise RuntimeError("Scryfall rate limit hit (HTTP 429); slow down and retry.")
    if r.status_code < 500:
        _cache[key] = (time.time(), r)
    return r


def _error(r: httpx.Response) -> str:
    try:
        return r.json().get("details", r.text)
    except ValueError:
        return r.text


def _slim(c: dict) -> dict:
    """Keep only the fields useful for deckbuilding."""
    faces = c.get("card_faces")
    oracle = c.get("oracle_text")
    if oracle is None and faces:
        oracle = "\n//\n".join(f"{f.get('name')}: {f.get('oracle_text', '')}" for f in faces)
    mana = c.get("mana_cost")
    if mana is None and faces:
        mana = " // ".join(f.get("mana_cost", "") for f in faces)
    out = {
        "name": c.get("name"),
        "mana_cost": mana,
        "cmc": c.get("cmc"),
        "type_line": c.get("type_line"),
        "oracle_text": oracle,
        "color_identity": c.get("color_identity"),
        "keywords": c.get("keywords") or None,
        "produced_mana": c.get("produced_mana"),
        "power": c.get("power"),
        "toughness": c.get("toughness"),
        "loyalty": c.get("loyalty"),
        "rarity": c.get("rarity"),
        "set": c.get("set"),
        "usd": (c.get("prices") or {}).get("usd"),
        "eur": (c.get("prices") or {}).get("eur"),
        "legal_in": [f for f, s in (c.get("legalities") or {}).items() if s == "legal"],
        "scryfall_uri": c.get("scryfall_uri"),
    }
    return {k: v for k, v in out.items() if v is not None}


@mcp.tool()
def search_cards(query: str, limit: int = 25, order: str = "edhrec", cheapest: bool = True) -> dict:
    """Search cards using Scryfall search syntax.

    Examples: 'c:g t:creature cmc<=3 f:commander', 'o:"draw a card" id<=ub',
    'is:commander c:wb', 't:land o:"add {G}" eur<0.5'.
    order: name, cmc, usd, eur, edhrec, rarity, released, power, toughness.
    cheapest: show each card's cheapest EUR printing (so prices are budget prices).
    Returns up to `limit` cards (max 175) plus the total match count.
    """
    limit = max(1, min(limit, 175))
    if cheapest and "prefer:" not in query:
        query = f"({query}) prefer:eur-low"
    r = _request("GET", "/cards/search", params={"q": query, "order": order})
    if r.status_code == 404:
        return {"total": 0, "cards": []}
    if r.status_code != 200:
        return {"error": _error(r)}
    body = r.json()
    cards = body["data"]
    # Pages are 175 cards, so one extra page is enough for the max limit.
    if len(cards) < limit and body.get("has_more"):
        nxt = _request("GET", body["next_page"].removeprefix("https://api.scryfall.com"))
        if nxt.status_code == 200:
            cards += nxt.json()["data"]
    return {
        "total": body.get("total_cards"),
        "cards": [_slim(c) for c in cards[:limit]],
        "warnings": body.get("warnings"),
    }


@mcp.tool()
def get_card(name: str, set_code: str | None = None) -> dict:
    """Look up a single card by name (fuzzy, typos are ok). Optionally pin a set code."""
    params = {"fuzzy": name}
    if set_code:
        params["set"] = set_code
    r = _request("GET", "/cards/named", params=params)
    return _slim(r.json()) if r.status_code == 200 else {"error": _error(r)}


@mcp.tool()
def get_cards(names: list[str]) -> dict:
    """Look up many cards by exact name at once (e.g. a whole decklist).

    Returns the found cards and the names that were not found.
    """
    found, missing = [], []
    unique = list(dict.fromkeys(n.strip() for n in names if n.strip()))
    for i in range(0, len(unique), 75):
        chunk = unique[i : i + 75]
        # The collection endpoint only matches split/double-faced cards by their front face.
        ids = [{"name": n.split(" // ")[0]} for n in chunk]
        r = _request("POST", "/cards/collection", json={"identifiers": ids})
        if r.status_code != 200:
            return {"error": _error(r)}
        body = r.json()
        found += [_slim(c) for c in body["data"]]
        lost = {nf.get("name") for nf in body.get("not_found", [])}
        missing += [n for n, i in zip(chunk, ids) if i["name"] in lost]
    return {"cards": found, "not_found": missing}


@mcp.tool()
def autocomplete(partial_name: str) -> list[str]:
    """Suggest up to 20 card names matching a partial name."""
    r = _request("GET", "/cards/autocomplete", params={"q": partial_name})
    return r.json().get("data", []) if r.status_code == 200 else []


@mcp.tool()
def random_card(query: str | None = None) -> dict:
    """Get a random card, optionally restricted by a Scryfall search query."""
    r = _request("GET", "/cards/random", params={"q": query} if query else None)
    return _slim(r.json()) if r.status_code == 200 else {"error": _error(r)}


def _name_queries(names: list[str], max_len: int = 900) -> list[str]:
    """Group names into '!"a" or !"b"' terms; Scryfall cuts queries off at ~1000 chars."""
    queries, current = [], ""
    for n in names:
        term = f'!"{n}"'
        if current and len(current) + len(term) + 4 > max_len:
            queries.append(current)
            current = ""
        current = f"{current} or {term}" if current else term
    return queries + ([current] if current else [])


def _search_all(query: str) -> list[dict]:
    """All raw cards matching a query, following pagination; [] when nothing matches."""
    r = _request("GET", "/cards/search", params={"q": query})
    cards = []
    while r.status_code == 200:
        body = r.json()
        cards += body["data"]
        if not body.get("has_more"):
            break
        r = _request("GET", body["next_page"].removeprefix("https://api.scryfall.com"))
    return cards


def _cheapest_prices(names: list[str]) -> dict[str, float]:
    """Map lowercased card name -> EUR price of its cheapest printing."""
    prices: dict[str, float] = {}
    for terms in _name_queries(names):
        for c in _search_all(f"({terms}) prefer:eur-low"):
            eur = (c.get("prices") or {}).get("eur")
            if eur:
                prices[c["name"].lower()] = float(eur)
    return prices


# Card roles from Scryfall's community oracle tags (https://scryfall.com/docs/tagger-tags).
_ROLES = {
    "ramp": "ramp",
    "card_draw": "draw",
    "removal": "removal",
    "board_wipes": "board-wipe",
    "counterspells": "counterspell",
    "tutors": "tutor",
    "recursion": "recursion",
    "protection": "protection",
}


def _roles(names: list[str]) -> dict[str, list[str]]:
    """Map role -> names of the given cards Scryfall tags with that role."""
    wanted = set(names)
    roles: dict[str, list[str]] = {}
    for role, tag in _ROLES.items():
        for terms in _name_queries(names):
            for c in _search_all(f"({terms}) otag:{tag}"):
                # !"Demonic Tutor" also matches 'Emeritus of Woe // Demonic Tutor'.
                if c["name"] in wanted:
                    roles.setdefault(role, []).append(c["name"])
    return roles


_LINE = re.compile(r"^\s*(?:(\d+)x?\s+)?(.+?)(?:\s+\([A-Za-z0-9]+\).*)?\s*$")
_BASICS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}
_SECTIONS = {"deck", "sideboard", "commander", "maybeboard", "companion"}
_TYPES = ["Creature", "Planeswalker", "Battle", "Instant", "Sorcery", "Artifact", "Enchantment", "Land"]
_COLORS = "WUBRG"
_SINGLETON = {"commander", "brawl", "oathbreaker"}


def _parse_decklist(decklist: str) -> dict[str, int]:
    """Parse '1 Sol Ring' / '4x Lightning Bolt (2X2) 117' lines into name -> count."""
    counts: dict[str, int] = {}
    for line in decklist.splitlines():
        line = line.strip()
        if not line or line.startswith(("//", "#")) or line.lower().rstrip(":") in _SECTIONS:
            continue
        m = _LINE.match(line)
        if m:
            counts[m.group(2)] = counts.get(m.group(2), 0) + int(m.group(1) or 1)
    return counts


def _lookup(names: list[str]):
    """Fetch cards; returns (card(name) -> dict | None, not_found) or an error dict."""
    lookup = get_cards(names)
    if "error" in lookup:
        return lookup
    by_name = {c["name"].lower(): c for c in lookup["cards"]}

    def card(n: str) -> dict | None:
        return by_name.get(n.lower()) or next(
            (c for k, c in by_name.items() if k.split(" // ")[0] == n.lower()), None
        )

    return card, lookup["not_found"]


def _front_type(c: dict) -> str:
    return (c.get("type_line") or "").split(" // ")[0]


@mcp.tool()
def check_deck(
    decklist: str,
    format: str = "commander",
    commander: str | None = None,
    budget_eur: float | None = None,
) -> dict:
    """Validate a decklist for a format (commander, standard, modern, pioneer, legacy, pauper, ...).

    decklist: one card per line, like '1 Sol Ring' or '4x Lightning Bolt (2X2) 117'.
    Lines starting with '//' or '#', and section headers like 'Sideboard', are ignored.
    For commander, pass the commander name to check color identity (it is also priced).
    Prices use each card's cheapest EUR printing (Cardmarket trend price).
    budget_eur: if given, reports whether the deck fits and how much it is over.
    Returns counts, illegal cards, off-identity cards, copy-limit violations,
    total EUR price, the most expensive cards and cards without a EUR price.
    """
    counts = _parse_decklist(decklist)
    looked_up = _lookup(list(counts) + ([commander] if commander else []))
    if isinstance(looked_up, dict):
        return looked_up
    card, not_found = looked_up

    fmt = format.lower()
    identity = set(card(commander)["color_identity"]) if commander and card(commander) else None
    priced = dict(counts)
    if commander and card(commander) and commander not in counts:
        priced[commander] = 1
    prices = _cheapest_prices([card(n)["name"] for n in priced if card(n)])
    not_legal, off_identity, too_many, unpriced = [], [], [], []
    costs: list[tuple[str, float]] = []
    for name, qty in priced.items():
        c = card(name)
        if not c:
            continue
        eur = prices.get(c["name"].lower())
        if eur is None:
            unpriced.append(name)
        else:
            costs.append((name, eur * qty))
    total_eur = sum(cost for _, cost in costs)
    for name, qty in counts.items():
        c = card(name)
        if not c:
            continue
        if fmt not in c.get("legal_in", []):
            not_legal.append(name)
        if identity is not None and not set(c.get("color_identity", [])) <= identity:
            off_identity.append(name)
        unlimited = c["name"] in _BASICS or "any number of cards named" in (c.get("oracle_text") or "")
        cap = 1 if fmt in _SINGLETON else 4
        if qty > cap and not unlimited:
            too_many.append(f"{qty}x {name}")

    result = {
        "card_count": sum(counts.values()) + (1 if commander and commander not in counts else 0),
        "not_found": not_found,
        "not_legal": not_legal,
        "off_color_identity": off_identity,
        "over_copy_limit": too_many,
        "total_eur": round(total_eur, 2),
        "most_expensive": [
            {"name": n, "eur": round(e, 2)} for n, e in sorted(costs, key=lambda x: -x[1])[:10]
        ],
        "no_eur_price": unpriced,
    }
    if budget_eur is not None:
        result["budget_eur"] = budget_eur
        result["within_budget"] = total_eur <= budget_eur
        result["over_budget_by_eur"] = round(max(0.0, total_eur - budget_eur), 2)
    return result


@mcp.tool()
def analyze_deck(decklist: str, commander: str | None = None) -> dict:
    """Analyze a deck's structure: mana curve, card types, roles, color pips vs. land sources.

    decklist: same format as check_deck. The commander, if given, counts toward the deck.
    Returns the curve of non-land cards by mana value, counts per card type,
    roles of non-land cards (ramp, card draw, removal, board wipes, counterspells,
    tutors, recursion, protection; from Scryfall's community oracle tags, so a few
    cards may be missing or tagged loosely), colored mana symbols in costs, colored
    sources among lands, and warnings (land count, thin ramp/draw/removal, colors
    with too few sources). Use check_deck for legality and price.
    """
    counts = _parse_decklist(decklist)
    if commander and commander not in counts:
        counts[commander] = 1
    looked_up = _lookup(list(counts))
    if isinstance(looked_up, dict):
        return looked_up
    card, not_found = looked_up

    total = sum(q for n, q in counts.items() if card(n))
    curve: dict[str, int] = {}
    types = {t: 0 for t in _TYPES}
    pips = {c: 0 for c in _COLORS}
    sources = {c: 0 for c in _COLORS}
    lands = nonland = 0
    mv_sum = 0.0
    colorless_lands: list[str] = []
    for name, qty in counts.items():
        c = card(name)
        if not c:
            continue
        front = _front_type(c)
        for t in _TYPES:
            if t in front:
                types[t] += qty
        if "Land" in front:
            lands += qty
            produced = [m for m in c.get("produced_mana") or [] if m in _COLORS]
            for m in produced:
                sources[m] += qty
            if not produced:
                colorless_lands.append(name)
            continue
        nonland += qty
        mv = int(c.get("cmc") or 0)
        mv_sum += mv * qty
        bucket = str(mv) if mv < 7 else "7+"
        curve[bucket] = curve.get(bucket, 0) + qty
        # Hybrid symbols like {W/U} count for both colors.
        for sym in re.findall(r"\{([^}]+)\}", c.get("mana_cost") or ""):
            for m in _COLORS:
                if m in sym:
                    pips[m] += qty

    nonland_qty = {
        card(n)["name"]: q for n, q in counts.items() if card(n) and "Land" not in _front_type(card(n))
    }
    roles = {
        role: {"count": sum(nonland_qty[n] for n in names), "cards": names}
        for role, names in _roles(list(nonland_qty)).items()
    }

    warnings = []
    commander_deck = total >= 90
    if commander_deck:
        # Common Commander guidelines; a deck's theme can justify going lower.
        for role, minimum in (("ramp", 8), ("card_draw", 8), ("removal", 5)):
            have = roles.get(role, {}).get("count", 0)
            if have < minimum:
                warnings.append(f"Only {have} {role.replace('_', ' ')} cards; {minimum}+ is common in Commander.")
    low, high = (34, 40) if commander_deck else (22, 27)
    if lands < low:
        warnings.append(f"Only {lands} lands; {low}-{high} is typical for a {total}-card deck.")
    elif lands > high:
        warnings.append(f"{lands} lands is a lot; {low}-{high} is typical for a {total}-card deck.")
    pip_total = sum(pips.values())
    for m in _COLORS:
        # A splash of one or two symbols is too small to judge.
        if pips[m] < 3 or not lands:
            continue
        # Rough rule: a color's share of sources should be at least ~60% of its share of pips.
        expected = lands * pips[m] / pip_total
        if sources[m] < 0.6 * expected:
            warnings.append(
                f"{m}: {pips[m]} pips but only {sources[m]} lands produce it (aim for ~{round(expected)})."
            )

    return {
        "card_count": total,
        "not_found": not_found,
        "lands": lands,
        "nonland_cards": nonland,
        "average_mana_value": round(mv_sum / nonland, 2) if nonland else 0,
        "curve": dict(sorted(curve.items(), key=lambda kv: (len(kv[0]), kv[0]))),
        "types": {t: n for t, n in types.items() if n},
        "roles": roles,
        "color_pips": {m: n for m, n in pips.items() if n},
        "land_sources": {m: n for m, n in sources.items() if n},
        "lands_without_colored_mana": colorless_lands,
        "warnings": warnings,
    }


@mcp.tool()
def find_alternatives(
    card_name: str,
    max_eur: float,
    commander: str | None = None,
    format: str = "commander",
    limit: int = 10,
) -> dict:
    """Find cheaper cards that could fill the same slot as a given card.

    Searches for cards of the same main type with a similar mana value (±1),
    within the commander's color identity (or the card's own colors), legal in
    the format and costing at most max_eur. Sorted by popularity (EDHREC).
    Read the returned oracle texts to pick ones that really do the same job.
    """
    original = get_card(card_name)
    if "error" in original:
        return original
    identity = original.get("color_identity", [])
    if commander:
        cmd = get_card(commander)
        if "error" in cmd:
            return cmd
        identity = cmd.get("color_identity", [])
    front = _front_type(original)
    main_type = next((t for t in _TYPES if t in front), None)
    mv = int(original.get("cmc") or 0)
    parts = [
        f"id<={''.join(identity).lower() or 'c'}",
        f"f:{format}",
        f"eur<={max_eur}",
        f'-!"{original["name"]}"',
    ]
    if main_type:
        parts.append(f"t:{main_type.lower()}")
    if main_type != "Land":
        parts += [f"mv>={max(0, mv - 1)}", f"mv<={mv + 1}"]
    result = search_cards(" ".join(parts), limit=limit, order="edhrec")
    return {"original": original, "query": " ".join(parts), **result}


@mcp.tool()
def export_deck(decklist: str, commander: str | None = None) -> dict:
    """Format a deck for import into MTG Arena, Moxfield or Archidekt.

    Fixes card names to their official spelling, puts the commander in its own
    section and sorts the rest by type and name. Returns the text to paste and
    any names that were not found (those are left out of the export).
    """
    counts = _parse_decklist(decklist)
    if commander:
        counts.pop(commander, None)
    looked_up = _lookup(list(counts) + ([commander] if commander else []))
    if isinstance(looked_up, dict):
        return looked_up
    card, not_found = looked_up

    def type_rank(c: dict) -> int:
        front = _front_type(c)
        return next((i for i, t in enumerate(_TYPES) if t in front), len(_TYPES))

    rows = sorted(
        ((card(n), q) for n, q in counts.items() if card(n)),
        key=lambda cq: (type_rank(cq[0]), cq[0]["name"]),
    )
    lines = []
    if commander and card(commander):
        lines += ["Commander", f"1 {card(commander)['name']}", "", "Deck"]
    lines += [f"{q} {c['name']}" for c, q in rows]
    return {"text": "\n".join(lines), "not_found": not_found}


@mcp.prompt(title="Build a budget Commander deck")
def build_budget_commander(request: str) -> str:
    """Build a 100-card Commander deck within a EUR budget.

    request: free text with the commander, budget and theme, e.g.
    'Xantcha, Sleeper Agent for €30, goad and graveyard recursion'.
    """
    return f"""Build a 100-card Commander deck from this request: {request}

From the request, work out the commander, the total budget in EUR (default €30 if none
is given; it includes the commander) and the play style or theme. If the commander is
unclear, ask me before building.

Work in these steps, using the Scryfall tools:
1. Look up the commander with get_card and summarize its strategy in two sentences.
2. Use search_cards (with eur<= filters) to find cards for the strategy: about 36 lands,
   10 ramp, 10 card draw, 8 removal/interaction, and the rest on the theme.
3. Run analyze_deck and fix any land-count or color-source warnings.
4. Run check_deck with the budget and the commander. If over budget, replace the
   most expensive cards using find_alternatives until it fits. Fix illegal or off-color cards.
5. Finish with export_deck and give me the decklist to paste, the total price,
   and a short explanation of how the deck plays."""


if __name__ == "__main__":
    mcp.run()


if __name__ == "__main__":
    mcp.run()
