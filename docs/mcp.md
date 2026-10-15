# Pellaeon and MCP

ChimeraX has a built-in `mcp` command that lets an outside assistant, such as Claude Desktop or Cursor, send commands to ChimeraX. Pellaeon works the other way round: the chat is inside ChimeraX, with the model you choose.

They also work together. Every Pellaeon analysis tool is a plain ChimeraX command, so an assistant on `mcp`, a `.cxc` script or `chimerax --nogui` can run it. No chat, no model, no key; the result goes to the Log.

- `pellaeon tool contacts #1 #2 chain A`: lost and gained contacts, drawn red and green
- `pellaeon tool compare #1 #2`: superpose and color by residue displacement
- `pellaeon tool annotate #1 P07550 variant`: ClinVar variants, or a UniProt feature such as `transmembrane`, `domain`, `binding`
- `pellaeon tool fetch alphamissense P07550`: AlphaMissense per position; `fetch conservation 1UBQ chain A` for ConSurf
- `pellaeon tool table ~/scores.csv column hydropathy`: your residue table, colored with a key
- `pellaeon tool tidy`: rearrange overlapping labels
- `pellaeon tool explain #1/A:87`: why this residue has its color
- `pellaeon tool list`: all of the above; `usage pellaeon tool contacts` shows the arguments

Needs Pellaeon 0.2.1 or newer ([install](install.md)). `usage mcp` tells you whether your ChimeraX has the `mcp` command; its [documentation](https://www.cgl.ucsf.edu/chimerax/docs/user/commands/mcp.html) covers the setup.
