The most serious problems are executable commands bypassing confirmation, scientifically incorrect residue mapping, and execution history that can misrepresent what actually happened.

I read the requested guides and traced the implementation and tests. Files changed externally during the review; the final checks used commit **`195f5ba`**, including its new ClinVar support. I modified no files.

Validation: **49 tests passed; one disk-writing cache test was excluded** to preserve read-only operation. I also reproduced several failures with in-memory fake executors. I did not run ChimeraX, Chimera, live providers, or a browser exploit.

**1. Correctness bugs**

1. **High — The automatic fallback can perform the wrong task on the wrong model.**  
   `_AA_TYPE_RE` matches any occurrence of “hydrophobic”; `_canned_fallbacks()` then executes a fixed coloring recipe against `#1`. I reproduced **“select hydrophobic residues in #2” → four coloring commands against #1**, followed by a success message about coloring. Classic applies its recipe without a model restriction.  
   **Fix:** remove automatic execution of this fallback, or require a narrowly recognized coloring intent and an explicitly resolved target.  
   Sources: [agent.py:69](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:69>), [agent.py:301](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:301>).

2. **High — Reopened conversations turn proposals into successful execution records.**  
   `_load_conversation()` drops tool results and sends assistant tool-call arguments to JavaScript. `loadConversation()` renders every command with a green dot and “Ran N commands.” Failed, skipped, canceled, and user-edited commands therefore become misleading history. A string-valued `commands` argument, accepted during live execution, also breaks history rendering because JavaScript calls `.map()` on it.  
   **Fix:** reconstruct history from calls joined to their results by ID; display the actual approved commands and execution status. Normalize argument types consistently.  
   Sources: [panel_base.py:554](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/panel_base.py:554>), [panel.js:334](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/ui/panel.js:334>).

3. **High — Stop can erase the record of commands that already executed.**  
   `_loop()` accumulates results locally until all calls in a response finish. If cancellation occurs between calls, those results are discarded; `_close_dangling_tool_calls()` marks every call as canceled before execution. I reproduced an executed `open 4hhb` subsequently recorded as “Cancelled … before this tool ran.” This also removes that successful command from exports.  
   **Fix:** commit completed results incrementally, then create cancellation results only for unfinished calls.  
   Sources: [agent.py:251](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:251>), [agent.py:322](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:322>).

4. **High — Structural comparison does not use the alignment’s residue correspondence.**  
   After MatchMaker superposition, displacement pairs are constructed from `(chain_id, residue_number)`. Two homologues with shifted numbering can produce incorrect pairs; insertion codes collide; corresponding chains with different IDs fail to pair. For multichain references, the first chain is selected automatically and its ID is imposed on both structures. The resulting “moving regions” can reflect correspondence mistakes.  
   **Fix:** retain alignment-derived atom/residue pairs, support separate reference/mobile chains, preserve insertion codes, and report alignment coverage.  
   Source: [analysis.py:38](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/analysis.py:38>).

5. **High — Explicitly nonexistent model IDs silently select another structure.**  
   `_find_structure()` falls back to the sole open structure even after an explicit ID fails. I reproduced `#99` resolving to the only open model, `#1`. Comparing `#1` with nonexistent `#2` can consequently resolve both operands to the same object.  
   **Fix:** fail for unresolved explicit identifiers; only infer the sole structure when the target is genuinely omitted. Reject self-comparison.  
   Source: [analysis.py:10](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/analysis.py:10>).

6. **High — Annotation targeting and success reporting are unreliable.**  
   UniProt positions become `#model:position` selectors, affecting every chain with that number, including unrelated proteins in a heteromer. No sequence mapping is performed; `numbered_ok = … or True` is unused. A warning is suppressed merely because the model name contains “alphafold” or the accession. After command failure, the return value still reports all features as colored/labeled, and the agent marks the tool successful because no top-level `error` exists. I reproduced that contradictory result.  
   **Fix:** map accession positions to specific chains/residues, count actual matches and completed operations, and propagate partial failure explicitly.  
   Sources: [analysis.py:197](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/analysis.py:197>), [agent.py:411](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:411>), [chimera_rest.py:197](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/classic/pellaeon_classic/chimera_rest.py:197>).

7. **Medium — Disulfide annotations become continuous residue intervals.**  
   All features with different start/end positions become `:start-end`. Applied to a disulfide between two cysteines, this colors the entire intervening sequence.  
   **Fix:** represent feature geometry explicitly: disulfides have endpoint residues, whereas domains and transmembrane regions span intervals.  
   Sources: [uniprot.py:156](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/uniprot.py:156>), [analysis.py:202](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/analysis.py:202>).

8. **High — Approved multiline Python executes only its first line.**  
   The confirmation card splits its textarea into trimmed lines, destroying indentation. The agent executes only `approved[0]`. I reproduced approval of `x = 1\nprint(x)` executing just `x = 1`; approving a function or loop definition will instead produce a syntax error.  
   **Fix:** distinguish Python-code approval from command-list approval and preserve the complete source string verbatim.  
   Sources: [panel.js:208](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/ui/panel.js:208>), [agent.py:416](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:416>).

9. **Medium — Classic’s Ollama Pull action crashes before its error handler.**  
   Classic imports `panel_base` as a top-level module, but `_pull_model()` uses `from .core.providers.ollama …`. I reproduced `ImportError: attempted relative import with no known parent package`. The import precedes `try`, so the panel receives no failure event.  
   **Fix:** import through the same native/classic compatibility mechanism used at module initialization, and include provider construction inside error handling.  
   Sources: [app.py:29](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/classic/pellaeon_classic/app.py:29>), [panel_base.py:287](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/panel_base.py:287>).

10. **High — Annotation performs network requests on ChimeraX’s GUI thread.**  
    The bridge schedules the entire annotation function on the main thread, including UniProt resolution/features and ClinVar requests and sleeps. An uncached lookup can freeze rendering and prevent the Stop button from being processed.  
    **Fix:** fetch and prepare annotations in the worker; dispatch only scene inspection/mutation to the main thread.  
    Sources: [bridge.py:244](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/bridge.py:244>), [analysis.py:113](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/analysis.py:113>), [analysis.py:179](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/analysis.py:179>).

11. **Medium — `ask_user` does not actually stop the remaining calls.**  
    Its branch sets `asked = True` and uses `continue`; subsequent calls in the same response execute. I reproduced “Which model?” being shown while `color #1 red` still ran.  
    **Fix:** stop execution immediately at the clarification boundary and mark subsequent calls deferred/unexecuted.  
    Source: [agent.py:254](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:254>).

12. **Medium — Export does not reproduce comparison, annotation, or reruns.**  
    Export includes only successful `run_commands` tool results. Commands executed inside analysis helpers and through the rerun button never enter that record. A chat that opens two models and compares them exports the opening commands but omits the superposition and displacement coloring.  
    **Fix:** maintain an executor-level execution journal shared by all mutation paths; explicitly record operations that cannot be replayed as ordinary commands.  
    Sources: [tool.py:154](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/tool.py:154>), [panel_base.py:145](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/panel_base.py:145>), [app.py:120](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/classic/pellaeon_classic/app.py:120>).

13. **High — The new ClinVar overlay discards correspondence information.**  
    It takes the first parseable protein change, retains no transcript/protein accession, and collapses records by integer position alone. The overlay then colors that position across the whole model without checking the reference amino acid. Records from different protein isoforms can therefore be conflated and mapped incorrectly. Additionally, the 2,000-result search limit is not reported as truncation.  
    **Fix:** retain transcript/protein identity and variant provenance, verify mapping/reference residues, distinguish variant count from affected-position count, and expose incomplete retrieval.  
    Sources: [clinvar.py:65](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/clinvar.py:65>), [analysis.py:119](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/analysis.py:119>).

**2. Security/privacy issues**

1. **Critical — Classic exposes unauthenticated command execution and saved-key forwarding.**  
   Binding to `127.0.0.1` limits network exposure, but POST actions validate neither credentials nor Origin/Host. `/act/rerun` reaches the executor directly. A simulated request with an unrelated Origin and Host received HTTP 200 and executed the fake command.

   Separately, `settings_test`/`list_models` accept a caller-supplied `base_url` and retrieve the saved key when `api_key` is empty. I verified that a supplied external endpoint receives a provider configured with the saved-key sentinel in its authorization header. This makes the unauthenticated API a credential-forwarding mechanism. Browser exploitability depends on browser restrictions; the server itself supplies no protection.

   **Fix:** require an unpredictable session credential, validate Host and Origin, authenticate SSE, and bind saved credentials to approved provider endpoints. Require authenticated JSON request bodies.  
   Sources: [app.py:235](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/classic/pellaeon_classic/app.py:235>), [panel_base.py:145](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/panel_base.py:145>), [panel_base.py:255](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/panel_base.py:255>), [openai_compat.py:27](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/providers/openai_compat.py:27>).

2. **High — Conversation IDs permit filesystem traversal.**  
   Delete/load paths concatenate an unchecked `id` with `.json`. An absolute ID or `../` sequence escapes the conversation directory; deletion can remove any writable JSON file at the resulting path. Classic exposes these actions through the unauthenticated HTTP dispatcher.  
   **Fix:** accept only generated IDs, reject separators/absolute paths, and verify resolved-path containment.  
   Sources: [panel_base.py:245](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/panel_base.py:245>), [panel_base.py:535](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/panel_base.py:535>).

3. **High — Analysis tools bypass the safety gate and interpolate executable syntax.**  
   `compare_structures` and `annotate` call executor methods directly, including in “Ask before every command” mode. Annotation interpolates unrestricted `color`; classic comparison interpolates unrestricted model arguments. An annotation color such as `red; close #1; #` is embedded directly into generated command strings. I verified this construction without executing host commands.

   **Fix:** route every mutation through one policy boundary, validate typed model/chain/color arguments, and use host parsing/quoting APIs for values.  
   Sources: [agent.py:407](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:407>), [analysis.py:204](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/analysis.py:204>), [chimera_rest.py:175](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/classic/pellaeon_classic/chimera_rest.py:175>).

4. **High — The command classifier has confirmed executable-file and file-writing holes.**  
   I verified that both `open https://example.org/review.py.gz` and `hbonds #1 saveFile /tmp/hbonds.txt` require no confirmation. The bundled host documentation explicitly supports compressed Python and the H-bond file-writing option. Matching only the outer suffix and first command word is insufficient. Abbreviated `open` and `log save` forms are also not normalized by the relevant checks.

   **Fix:** inspect the underlying format after compression, canonicalize host command names/options, and classify effect-bearing options as well as verbs. Treat unknown executable formats conservatively.  
   Sources: [safety.py:55](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/safety.py:55>), [safety.py:119](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/safety.py:119>), [open.html:833](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/tests/fixtures/docs_html/open.html:833>), [hbonds.html:421](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/tests/fixtures/docs_html/hbonds.html:421>).

5. **High — Disabled capabilities remain executable; missing confirmation callbacks approve everything.**  
   `allow_python` and `vision` control advertised tool definitions, but `_dispatch()` does not enforce either setting. I reproduced execution of a disabled Python tool and screenshot acquisition with vision disabled. Python still asks when a callback exists; screenshots do not. `_confirm()` returns approval when no callback exists, including for risky commands.  
   **Fix:** enforce capability checks at dispatch and deny confirmation-required actions when no approval mechanism exists.  
   Sources: [tools.py:175](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/tools.py:175>), [agent.py:416](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:416>), [agent.py:436](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:436>).

6. **Medium — “Never ask (this session)” can persist.**  
   The dropdown writes `settings.autonomy`; native settings include autonomy in `AUTO_SAVE`. Classic also persists it when settings are saved. The label promises a narrower duration than the implementation enforces.  
   **Fix:** keep unrestricted mode in transient runtime state and reset it on startup.  
   Sources: [panel.html:24](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/ui/panel.html:24>), [panel_base.py:169](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/panel_base.py:169>), [settings.py:19](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/settings.py:19>).

7. **Prompt-injection protection is a prompt instruction, not an execution boundary.**  
   The system prompt correctly labels tool results and metadata as data. However, metadata/docs enter plaintext context, and summaries derived from the transcript are appended to the system prompt. This provides opportunities for injected text to influence subsequent decisions; the gate bypasses above make that influence consequential. I am not claiming a tested model-level exploit.  
   **Fix:** retain provenance and explicit untrusted-data boundaries, validate summary structure, and enforce authorization independently of model output.  
   Sources: [prompt.py:27](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/prompt.py:27>), [prompt.py:164](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/prompt.py:164>), [agent.py:145](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:145>).

8. **Privacy claims need narrower wording and better transport/storage handling.**  
   Keys are sensibly separated from session serialization, and the fallback secret file requests mode 0600. However, classic sends keys inside URL query parameters; conversation files use ordinary filesystem permissions and retain prompts, context, tool output, and screenshots. Local-model use still performs external UniProt/ClinVar lookups, so “nothing leaves your computer” is inaccurate.  
   **Fix:** move action payloads into request bodies, apply private permissions and retention controls to transcripts, and distinguish local inference from external biological-data queries.  
   Sources: [panel.js:12](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/ui/panel.js:12>), [secrets.py:30](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/secrets.py:30>), [panel_base.py:507](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/panel_base.py:507>), [uniprot.py:97](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/uniprot.py:97>).

9. **Installer: HTTPS is present, but artifact selection and reproducibility are weak.**  
   `install_pellaeon.py` selects the first `.whl` from the latest release, without checking distribution identity, compatibility, expected digest, or correspondence to the installer’s release. It reads the entire download into memory and leaves its temporary directory behind. This is a supply-chain hardening issue, not evidence that GitHub metadata is currently malicious.  
   **Fix:** select a specific compatible Pellaeon artifact, pin the release, verify an expected digest, stream with a size bound, and clean up in `finally`.  
   Source: [install_pellaeon.py:21](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/install_pellaeon.py:21>).

**3. Robustness of the agent loop with small local models**

- **Read-only activity can mask complete inaction.** `called_tool` includes documentation/state lookups. I reproduced `get_state` followed by “Colored red” ending successfully with zero mutations. Conversely, “Can you color it red?” is excluded from action detection because it starts with “can” or ends in `?`. Track completed effects, not merely tool calls.  
  Sources: [agent.py:87](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:87>), [agent.py:172](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:172>), [agent.py:265](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:265>).

- **Recovery nudges remove the scene context precisely when it is needed.** Nudges are appended as new user messages without context. Every provider includes context only on the last user message. I verified that an Ollama request after a nudge loses the original scene block entirely. Refresh context for recovery attempts or represent internal nudges separately.  
  Sources: [agent.py:187](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:187>), [ollama.py:24](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/providers/ollama.py:24>).

- **Loop limits are not a turn-wide budget.** Each `_loop()` invocation allows `max_tool_rounds + 1` responses, and nudges start new loops with fresh error counters. Successful lookups between failures reset the consecutive-error count. Repeated successful commands, repeated lookups, and repeated declined proposals are not deduplicated. Add a total turn budget and explicit progress/decline tracking.  
  Sources: [agent.py:170](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:170>), [agent.py:215](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:215>), [agent.py:450](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:450>).

- **Heuristics count proposed commands rather than successful commands.** `_commands_since()` reads assistant arguments, including skipped and failed commands. “Only” also triggers on requests such as “color only chain B,” potentially prompting unnecessary hiding. Use execution results and narrowly scoped intent checks.  
  Sources: [agent.py:178](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:178>), [agent.py:331](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:331>).

- **Stop is cooperative only between selected operations.** Network cancellation is checked after a line arrives, not while connecting/waiting. Executor command batches receive no cancellation token. Main-thread waits have no default timeout, and compaction calls the provider with `cancel=None`. Stop can therefore remain pending through network waits, entire batches, or summarization.  
  Sources: [http.py:75](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/http.py:75>), [agent.py:467](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:467>), [bridge.py:35](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/bridge.py:35>), [agent.py:542](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:542>).

- **Compaction does not bound the actual request.** It runs after a completed turn, excluding clarification/error returns. Its estimate excludes the system prompt, accumulated summary, images, and opaque thinking blocks. Summaries are appended forever rather than recursively compacted. A long single turn can exceed a small model’s context before compaction occurs. Budget the fully serialized request before each provider call.  
  Sources: [agent.py:167](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:167>), [agent.py:504](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:504>).

- **Ollama thinking text can become the final answer.** If content and calls are empty, the adapter substitutes the last 1,500 characters of thinking. This bypasses the empty-answer recovery and presents unfinished deliberation as a reply. Treat thinking-only completion as missing final output.  
  Source: [ollama.py:93](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/providers/ollama.py:93>).

- **Malformed/truncated tool calls lack a strict validation boundary.** Adapters salvage JSON or produce `_raw`; dispatch uses permissive defaults such as comparison targets `#1`/`#2`. The final “stop after errors” provider response is also accepted without checking for unexpected tool calls. Validate tool names, required fields, types, and completion status before execution or history insertion.  
  Sources: [base.py:67](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/providers/base.py:67>), [agent.py:407](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:407>), [agent.py:276](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/agent.py:276>).

- **Classic reconnects do not restore an active interaction.** SSE has no event replay; `ready` sends settings/state but not the current transcript, busy status, or pending confirmations. Refreshing during confirmation leaves the worker waiting on a card the new page cannot see. Its threaded server also invokes the shared controller without serializing `submit()` and related mutations. Add a reconnect snapshot and a serialized controller queue.  
  Sources: [app.py:210](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/classic/pellaeon_classic/app.py:210>), [panel_base.py:107](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/panel_base.py:107>), [panel_base.py:387](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/panel_base.py:387>).

**4. Panel UX improvements a structural biologist would notice**

- **Make model and chain targeting explicit.** The scene strip truncates at six models/six chains and is not interactive. Add expandable model/chain rows with visibility, residue ranges, and “use as target/reference/mobile” actions. This would reduce ambiguity in complexes and comparisons.  
  Source: [panel.js:237](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/ui/panel.js:237>).

- **Preserve exact residue identity and larger selections.** The selection bar disappears above three residues. Selection serialization drops insertion codes and truncates long selectors with an ellipsis; picked-residue prompts reconstruct identity from prose. Support domain-sized selections and retain full machine-readable identifiers separately from shortened display text.  
  Sources: [tool.py:95](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/tool.py:95>), [bridge.py:339](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/bridge.py:339>), [panel.js:371](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/ui/panel.js:371>).

- **Give comparison/annotation dedicated result cards.** Show reference/mobile chains, fit coverage, RMSD definition, displacement legend, annotation source, mapped/unmapped counts, and residue links that select/focus the scene. Currently these tools collapse into short generic summaries; classic comparison even inherits a summary describing zero paired residues and `None` mean shift.  
  Sources: [panel_base.py:583](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/panel_base.py:583>), [panel.js:191](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/ui/panel.js:191>).

- **Keep warnings and failures visible.** Command rows omit warnings; non-command tools hide detailed errors; rerun results disappear after a 2.6-second toast. Scientific caveats and partial failures should remain inspectable alongside the affected operation.  
  Sources: [panel.js:183](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/ui/panel.js:183>), [panel.js:232](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/ui/panel.js:232>), [panel.js:36](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/ui/panel.js:36>).

- **Show what Stop actually stopped.** Display completed, failed, skipped, and still-running operations; distinguish stopping the assistant from stopping an animation. Disable stale approval/question controls once their interaction ends.  
  Sources: [panel.js:114](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/ui/panel.js:114>), [panel.js:198](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/ui/panel.js:198>), [panel.js:220](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/ui/panel.js:220>).

- **Implement browser clipboard copy for classic.** Its copy button currently calls a server action that tells users to select and copy manually. Use the browser clipboard API with a fallback.  
  Sources: [panel.js:535](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/ui/panel.js:535>), [app.py:106](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/classic/pellaeon_classic/app.py:106>).

- **Improve keyboard access and preserve drafts until acceptance.** Provider cards, model pills, and welcome examples are clickable non-button elements. The input has no explicit label; status updates lack live-region semantics. Submission clears the draft before server acceptance, while classic ignores non-2xx fetch responses.  
  Sources: [panel.js:253](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/ui/panel.js:253>), [panel.js:377](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/ui/panel.js:377>), [panel.html:71](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/ui/panel.html:71>).

**5. Architecture/maintainability and missing tests**

The pure-Python core, neutral message schema, fake executors, and shared panel are useful foundations. The central weakness is that **policy, execution, and recording are separate paths that different features can bypass**.

- Introduce one execution service responsible for validation, confirmation, cancellation, results, and journaling. Analysis helpers should prepare operations and consume structured results.
- Split `PanelBase` into conversation storage, provider configuration, interaction state, and transport adapters. Its current mixture produces both the classic import failure and unsafe reuse of UI actions as HTTP endpoints.
- Make edition capabilities explicit. Classic receives ChimeraX-oriented tool descriptions, comparison promises, and some nudges despite different behavior. Annotation kind mappings and export implementations are duplicated.
- Use atomic persistence with unique IDs. Conversation filenames have only second-resolution timestamps, writes truncate in place, and failed provider rebuilding can discard the in-memory agent. Sources: [panel_base.py:203](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/panel_base.py:203>), [panel_base.py:499](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/panel_base.py:499>).
- Strengthen cache invalidation. Knowledge cache keys use collection counts and guide lengths rather than content hashes, so same-size edits and registry changes can reuse stale material. Source: [knowledge.py:452](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/src/core/knowledge.py:452>).

The highest-value missing tests are:

1. **Policy adversarial tests:** compressed scripts, effect-bearing options, injected analysis arguments, disabled tools, missing approval callbacks, and reruns.
2. **Execution-history tests:** cancellation after the first of several calls; edited/skipped/failed commands after reload; export completeness.
3. **Scientific fixtures:** renumbered homologues, insertion codes, different chain IDs, heteromers, missing residues, self-comparison, disulfide endpoints, and isoform-specific annotations.
4. **Controller/browser tests:** multiline Python approval, string-valued command history, reconnect during confirmation, failed submission preservation, and concurrent classic requests.
5. **Agent recovery tests:** read-only tools followed by false success, polite action requests, repeated declines, nudge context retention, and turn-wide budgets.
6. **Transport/security tests:** hostile Origin/Host, traversal IDs, saved-key endpoint substitution, interrupted streams, and cancellation during blocked reads.

Existing tests sometimes verify a weaker property than their name implies: the cancellation test checks only matching call/result IDs, not truthful execution status; the optional-Python test checks tool advertisement, not dispatch enforcement. Some scenario checks likewise inspect proposed commands instead of completed results.  
Sources: [test_agent.py:280](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/tests/test_agent.py:280>), [test_agent.py:156](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/tests/test_agent.py:156>), [scenarios.py:49](</run/media/yamir/bio/Pellaeon Alpha/Pellaeon_Beta/tests_chimerax/scenarios.py:49>).

**6. Top 10 prioritized suggestions**

| Priority | Suggestion | Impact / effort |
|---|---|---|
| 1 | Authenticate classic HTTP/SSE, validate Origin/Host, and prevent saved-key forwarding to arbitrary endpoints. | Critical / medium |
| 2 | Put every scene mutation behind one validated confirmation and execution boundary. | Critical / large |
| 3 | Fix residue correspondence for comparison and annotation; reject unresolved explicit targets. | High scientific impact / large |
| 4 | Make completed execution results authoritative for cancellation, history, and export. | High / medium |
| 5 | Remove the broad automatic coloring fallback and replace proposal-based success heuristics. | High / small–medium |
| 6 | Enforce disabled capabilities and make unavailable confirmation fail closed. | High / small |
| 7 | Move annotation network work off the GUI thread; propagate cancellation through requests and batches. | High / medium |
| 8 | Preserve multiline Python intact and fix classic’s Pull import. | High functional impact / small |
| 9 | Add a turn-wide budget, retain recovery context, and compact the actual serialized request before sending. | High for local models / medium |
| 10 | Add explicit scientific result cards and regression tests covering policy, mapping, history, and reconnect behavior. | High / medium–large |