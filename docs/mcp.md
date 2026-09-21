# Pellaeon and ChimeraX's mcp command

In December 2025 ChimeraX gained an `mcp` command that lets an assistant running outside ChimeraX, such as Claude Desktop, Cursor or VS Code Copilot, send it commands over the Model Context Protocol. Pellaeon is a chat panel inside ChimeraX with its own model connection. They solve different problems, and since 0.2.1 they can be used together.

## Side by side

<!--MCP_DIAGRAM-->

## What each one is for

| | ChimeraX `mcp` | Pellaeon |
|---|---|---|
| Where the assistant lives | A desktop app or editor outside ChimeraX | A panel docked inside ChimeraX |
| How it reaches ChimeraX | A bridge process and the REST server on a port | In the same process, nothing to start |
| Models | Whatever the host app runs (Claude Desktop needs a Claude subscription; Cursor and Copilot theirs) | Ollama on your computer (free, private), Mistral and Gemini free tiers, or paid Claude, OpenAI, Mistral keys |
| Platforms | Claude Desktop is Mac and Windows; Cursor and VS Code on Linux too | Wherever ChimeraX runs |
| What the assistant sees | Model list, shown items, chain info, command docs, and the log text of each command | The same, plus the selection, your loaded tables, the residue-level journal (why is this red?), and the indexed docs of your ChimeraX version |
| When a command fails | The host assistant decides what to do | The error goes back with the real usage and a suggestion, for a bounded number of retries |
| Before something destructive | Nothing in the bridge stops it | Close, delete, save and scripts wait for an editable OK card; a review-every-command mode exists |
| Analysis beyond commands | Not included; the assistant writes commands | UniProt features, ClinVar, AlphaMissense, ConSurf, table overlays, contact comparison, label tidying, figures with provenance |
| Where the record goes | The host app's chat | Command cards in the panel with copy, re-run and docs links; the chat exports as a `.cxc` script |

If you already work in Cursor or Claude Desktop all day and want to steer ChimeraX from there, `mcp` is the right tool and it is built in. If you want the conversation next to the 3D view, a free or local model, and the safety and analysis pieces above, use Pellaeon. Both can be active in the same session.

## Using them together

Pellaeon's analysis tools are registered as ordinary ChimeraX commands, so anything that can run a ChimeraX command can run them: the `mcp` bridge's `run_command` tool, a `.cxc` script, a `runscript`, or the command line. No chat, no model, no key.

```
pellaeon tool list
pellaeon tool contacts #1 #2 chain A            # lost and gained contacts, drawn red and green with a key
pellaeon tool contacts #1 #2 restrict :HEM      # what the heme touches in one form but not the other
pellaeon tool compare #1 #2 chain A             # superpose and color by residue displacement
pellaeon tool annotate #1 P07550 variant        # ClinVar missense variants on the structure
pellaeon tool annotate #1 P07550 transmembrane  # UniProt features
pellaeon tool fetch alphamissense P07550        # AlphaMissense pathogenicity per position
pellaeon tool fetch conservation 1UBQ chain A   # ConSurf conservation grades
pellaeon tool table ~/scores.csv column hydropathy palette viridis
pellaeon tool tidy                              # rearrange overlapping labels
pellaeon tool explain #1/A:87                   # why this residue has its color
```

Each command writes a one-line summary and the full result to the Log, which is what the `mcp` bridge returns to the assistant. So an assistant on the bridge can ask ChimeraX to "run `pellaeon tool contacts #1 #2`" and reason about the answer, the same result the Pellaeon panel's model would get. `usage pellaeon tool contacts` shows the arguments. When the Pellaeon panel is open, the two share state: a table loaded in the panel is available to `pellaeon tool table`, and colors set by either are explained by `pellaeon tool explain`.

## What is not shared

Pellaeon's model does not go through MCP and the `mcp` bridge does not use Pellaeon's model. Keys, autonomy settings and conversation history belong to the panel. Pellaeon does not read the bridge's traffic and the bridge does not see the panel's chat.

## Requirements

- The `mcp` command: a ChimeraX from December 2025 or later (type `usage mcp` to check), and a supported host app configured with `mcp setup`. See the [ChimeraX documentation](https://www.cgl.ucsf.edu/chimerax/docs/user/commands/mcp.html).
- `pellaeon tool` commands: Pellaeon 0.2.1 or newer, installed as described on the [install page](install.md). They work in `chimerax --nogui` as well.
