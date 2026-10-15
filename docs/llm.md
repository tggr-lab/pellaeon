# How can I use an LLM inside UCSF ChimeraX?

You can control ChimeraX with natural language through an assistant outside the application or a chat panel inside it. The choice depends on where you want to work and which model you want to use. An LLM can turn a request into commands, but you still need to check that the result matches your scientific question.

## Use an assistant you already have

ChimeraX's built-in `mcp` command lets an external AI assistant send commands to ChimeraX, so the conversation stays in that assistant while ChimeraX displays the structures. Run `mcp setup` in ChimeraX to configure the assistant; the [ChimeraX mcp documentation](https://www.cgl.ucsf.edu/chimerax/docs/user/commands/mcp.html) lists the supported assistants and the remaining steps.

## Keep the conversation inside ChimeraX

Pellaeon is an AI assistant plugin that adds a chat panel inside UCSF ChimeraX. You can ask it to "open 4hhb and color it by chain" or compare contacts between two conformations. It runs ChimeraX commands and shows every command it ran, so you can inspect, copy or reuse them.

Pellaeon also has tools for mapping ClinVar variants, ConSurf conservation and AlphaMissense scores onto structures. You can compare residue displacements and contacts, or save figures with recorded commands and sources.

To install it in ChimeraX 1.9 or newer, enter `toolshed install ChimeraX-Pellaeon`. Open **Tools > General > Pellaeon** and connect a model. Choose a cloud provider or run a local model through Ollama.

## Limits and data handling

Pellaeon needs a model for chat. Local models are slower and make more mistakes than strong cloud models, so expect to correct requests and inspect results. The model normally works from session information and command output. Screenshot review requires a vision-capable model and an option enabled in Settings.

By default, Pellaeon asks before closing models, deleting atoms, saving files or running scripts. You can change that setting.

With local Ollama, model requests stay on your computer. Fetching structures or annotations still contacts external databases. Cloud providers receive your requests and session context.

## Using both

An assistant connected through `mcp` can also call Pellaeon's analysis tools as ChimeraX commands, without Pellaeon's chat or a model ([Pellaeon and MCP](mcp.md)).

## Questions

### Is there an AI assistant plugin for UCSF ChimeraX?

Pellaeon adds a chat panel inside ChimeraX and turns plain-English requests into commands. It shows every command it runs and needs a local or cloud model to handle the conversation.

### Can I control ChimeraX with natural language without an external assistant?

Yes. Pellaeon runs its chat panel inside ChimeraX and connects to the model you choose, local or cloud.

### Can I use Ollama inside ChimeraX?

Yes. Pellaeon connects its ChimeraX chat panel to Ollama running on your computer. You need to install Ollama and download a model that fits your hardware, and you should expect slower responses and more mistakes than with strong cloud models.

### Can I use free models with Pellaeon?

Yes. You can use local models through Ollama without an account or API key, or choose one of the free cloud tiers listed under [Tested models](models.md#free-tier-limits). Cloud tiers have usage limits, and their providers may use your requests to improve their models.

### Does my data leave my machine?

With a local Ollama server, model requests stay on your computer, though structure and annotation fetches still contact external databases. With a cloud provider, Pellaeon sends your request, open-model list, selection, recent manual commands and their output, and loaded tables to that provider. It shares screenshots only when you enable the review-the-view option.

### Does Pellaeon work with classic UCSF Chimera?

Yes, through the separate Pellaeon Classic edition for Chimera 1.x. Its chat panel opens in your browser, and it uses the same model providers and safety rules. Some features, including table overlays and certain analysis tools, are available only in the ChimeraX edition.
