# Pellaeon and the mcp command

**In short.** ChimeraX's built-in `mcp` command lets an assistant you already use, such as Claude Desktop or Cursor, send commands into ChimeraX. Pellaeon puts the assistant inside ChimeraX, with any model including free and local ones, guard rails, and analysis tools. They are not rivals: Pellaeon's tools are ChimeraX commands, so an assistant on `mcp` can call them too.

## Side by side

<!--MCP_DIAGRAM-->

## Which one

- **You live in Claude Desktop, Cursor or VS Code and want to steer ChimeraX from there:** use `mcp`. It is built in, needs a Claude, Cursor or Copilot subscription, and Claude Desktop runs on Mac and Windows only.
- **You want the conversation next to the 3D view, a free or local model, confirmation before anything destructive, and the analysis below:** use Pellaeon.
- **Both at once** works; they do not share keys, chats or settings.

## Use both: Pellaeon's tools as commands

Anything that can run a ChimeraX command can run these, including the `mcp` bridge's `run_command`, a `.cxc` script and `chimerax --nogui`. No chat, no model, no key. The summary and full result go to the Log, which is what `mcp` returns to the assistant.

- `pellaeon tool contacts #1 #2 chain A`: lost and gained contacts, drawn red and green
- `pellaeon tool compare #1 #2`: superpose and color by residue displacement
- `pellaeon tool annotate #1 P07550 variant`: ClinVar variants; or a UniProt feature such as `transmembrane`, `domain`, `binding`
- `pellaeon tool fetch alphamissense P07550`: AlphaMissense per position; `fetch conservation 1UBQ chain A` for ConSurf
- `pellaeon tool table ~/scores.csv column hydropathy`: your residue table, colored with a key
- `pellaeon tool tidy`: rearrange overlapping labels
- `pellaeon tool explain #1/A:87`: why this residue has its color
- `pellaeon tool list`: all of the above; `usage pellaeon tool contacts` shows the arguments

Needs Pellaeon 0.2.1 or newer ([install](install.md)). The `mcp` command is in ChimeraX releases from December 2025 on; `usage mcp` tells you if yours has it, and [its documentation](https://www.cgl.ucsf.edu/chimerax/docs/user/commands/mcp.html) covers the setup.
