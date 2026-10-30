# mtg-deckbuilder-mcp

An MCP server that lets Claude build Magic: The Gathering decks with you using
card data from [Scryfall](https://scryfall.com): search cards, analyze the
mana curve and colors, check legality and a budget in euros, find cheaper
replacements and export the list for MTG Arena, Moxfield or Archidekt.

## What Claude can do with it

| Tool | What it does |
| --- | --- |
| `search_cards` | Search with [Scryfall syntax](https://scryfall.com/docs/syntax), e.g. `c:g t:creature cmc<=3 f:commander eur<0.5`. Shows the cheapest printing's price. |
| `get_card` | Look up one card by name (typos are ok). |
| `get_cards` | Look up a whole list of cards at once. |
| `check_deck` | Check a decklist for a format: illegal cards, cards outside the commander's colors, too many copies, total price in EUR and whether it fits a budget. |
| `autocomplete` | Suggest card names from a partial name. |
| `random_card` | A random card, optionally matching a search. |
| `analyze_deck` | Mana curve, card type counts, roles (ramp, card draw, removal, board wipes, counterspells, tutors, recursion, protection), colored mana symbols vs. land sources, and warnings like too few lands, too little ramp or removal, or too few sources for a color. |
| `find_alternatives` | Cheaper cards of the same type and similar mana value within your colors, under a price cap. |
| `export_deck` | Fixes card names and formats the list for import into MTG Arena, Moxfield or Archidekt. |

It also has a ready-made prompt, **Build a budget Commander deck**: pick it
from the prompt menu in Claude Desktop (the **+** button in the chat box),
enter a commander, a budget and optionally a theme, and Claude builds,
analyzes, checks and exports the deck step by step.

Prices are Cardmarket trend prices (EUR) via Scryfall, updated daily, using
each card's cheapest printing.

## Setup (Claude Desktop)

Works on the free plan of Claude, but needs the **Claude Desktop** app — the
claude.ai website cannot run local tools.

1. Install [Claude Desktop](https://claude.ai/download) and sign in.
2. Install [uv](https://docs.astral.sh/uv/) (runs the server and installs its
   Python dependencies automatically):
   - Windows: `winget install astral-sh.uv`
   - macOS: `brew install uv`
3. In Claude Desktop open **Settings → Developer → Edit Config** and add:

   ```json
   {
     "mcpServers": {
       "scryfall": {
         "command": "uv",
         "args": [
           "run",
           "--script",
           "https://raw.githubusercontent.com/Fudge0wn/mtg-deckbuilder-mcp/main/server.py"
         ]
       }
     }
   }
   ```

   If the file already has an `mcpServers` section, add the `scryfall` entry
   inside it.

4. Quit Claude Desktop completely (also from the system tray / menu bar) and
   start it again. The Scryfall tools appear under the tools icon in the chat
   box.

### Running a local copy instead

If you cloned the repo, point `args` at your file instead of the URL:

```json
"args": ["run", "--script", "C:/path/to/mtg-deckbuilder-mcp/server.py"]
```

### Claude Code

```sh
claude mcp add --scope user scryfall -- uv run --script /path/to/mtg-deckbuilder-mcp/server.py
```

## Example prompts

- "Find green ramp creatures under 3 mana that are legal in Commander and cost
  under €0.50."
- "Build a Commander deck with Meren of Clan Nel Toth for around €30 total.
  Check it with a €30 budget and swap the most expensive cards until it fits."
- "Here is my decklist: … Check it for Commander with Kenrith as commander."

Decklists can be pasted in the usual export format (`1 Sol Ring`,
`4x Lightning Bolt (2X2) 117`), so lists from Moxfield or Archidekt work as is.

## Troubleshooting

- **Tools don't show up:** check **Settings → Developer** for the server's
  status and logs, and make sure you fully restarted Claude Desktop.
- **`uv` not found:** Claude Desktop may not see your PATH. Use the full path
  to uv as `"command"` (find it with `where uv` on Windows or `which uv` on
  macOS).
- **Hitting message limits:** deckbuilding uses many tool calls, and the free
  plan has low limits. Ask for fewer, more targeted searches.

## Notes

- No API key or account is needed; Scryfall's API is public.
- The server follows Scryfall's
  [API guidelines](https://scryfall.com/docs/api): it identifies itself, waits
  100 ms between requests and caches responses for 24 hours.
- Card data comes from Scryfall, and prices are Cardmarket prices provided by
  Scryfall. This project is not affiliated with Scryfall, Cardmarket or Wizards
  of the Coast.
