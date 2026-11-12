# Step 4: local extraction and evidence/consistency

`auris extract --transcript T.json --intake I.json --out DIR [--model ID]`
uses the pinned local GGUF and an owned `llama-server`, then unloads it before
mapping validation starts. DIR must be new or empty and mode 0700. The two
artifacts, `extraction.json` and `validation.json`, use the private atomic
0600 writer. No final Bundle is written by this command. Existing setup
artifacts are required: terminology SQLite index and the pinned R4 package
for unit sets. Missing validator dependencies instead produce NOT_CHECKED
with the runner's reason. No inference or test path downloads anything.

## Backend, model pins and ownership

`extract/backend.py` defines the `Backend` protocol, `HttpBackend`,
`ManagedLlamaServer` and `FakeBackend`. The HTTP backend accepts only
`http://127.0.0.1:<port>`; it disables proxies and redirects. The unchanged
step-2a schema is sent as a strict OpenAI-compatible JSON Schema response
format. HTTP diagnostics are not copied into application logs. Model
stdout/stderr go to DEVNULL; raw completion responses live only in the
private artifact. Invalid input validation errors do not echo input values.

The managed server verifies the GGUF SHA-256 (and size when pinned), resolves
`AURIS_LLAMA_SERVER` before PATH, records `--version`, binds an OS-selected
loopback port, and waits for `/health` with a timeout. Flags are `-c 8192
-ngl 99 --no-webui` plus the selected candidate's `--reasoning off`. The
request also sets `enable_thinking=false`. Runtime versions differing from
Claude's tested `0.9.0 (build 12110, commit cd959bae9)` warn and remain
recorded. Context exit reaps the process on success, startup failure, or
inference exceptions; termination escalates to kill after five seconds.
The version probe also has a timeout. Tests simulate crashes and exit races.

`/props` supplies a chat-template SHA-256 when available; an unavailable
hash is explicitly null with a metadata warning. A background sampler uses
`ps` process RSS, in bytes, throughout startup/inference. This is a sampled
server-process peak, not a whole-pipeline or GPU/unified-memory peak. It
cannot establish compliance with the 12 GB pipeline budget on a 16 GB Mac.
Unmeasurable RSS remains null. Startup and extraction timings are recorded;
the CLI's run wall time includes model startup and unload.

Qwen3-8B Q4_K_M remains the default (`qwen3-8b-q4_k_m`). Existing default
pins are retained and must match exactly one default candidate. The supplied
Gemma metadata is added as `gemma-4-e4b-it-q4_0`, comparator only. Both model
licences are Apache-2.0, using the owner/Claude-provided verification for
Gemma. Its Q4_0 versus Qwen's Q4_K_M is a known confound; no official
Q4_K_M from ggml-org was supplied. Text-only inference needs no mmproj.
Neither candidate is downloaded by this step. No Python dependencies were
added; the existing external llama-server runtime is MIT.

## Prompt, parsing and context budgets

The prompt is `src/auris/extract/prompts/extraction_v1.md`. Its exact UTF-8
SHA-256 is:

```text
d87f75a09bf8718eb3f5ecab3789b62236a22949a990402e0100dedf1127be23
```

It defines all nine kinds, nullable fields, polarity, experiencer,
temporality including resolved, source role, medication intent, and verbatim
segment evidence. It includes fictional contrastive examples and both
measured regressions. Transcript text is data rather than instructions.
Segments render as `[s3 | patient | 00:42.1–00:45.0] text`; embedded CR/LF
are escaped to preserve one line per segment. Prompt hash and rendering
are covered by a fixed snapshot/hash test.

The prompt has 4,076 characters and a **1,359-token conservative estimate**.
No server-measured count is available from this sandbox. The unchanged
compact schema is 15,024 characters (5,008 estimated tokens). We chose
`max_tokens=1024`, rather than the suggested example 4096: the latter cannot
fit prompt + schema + output + headroom in 8192 under the fallback estimate.
Temperature remains the frozen candidate 0; this step does not claim it has
been empirically confirmed. A dense encounter may hit the output cap and
must fail visibly; the real-model probe is needed before changing this pin.

Whole encounters use one call when prompt + schema + output cap + 256 tokens
of headroom fit. `/tokenize` measures the input parts when available; the
fallback reserves one token per three ASCII characters and one per non-ASCII
UTF-8 byte. Counts are labelled measured versus estimated. The reserve
includes the schema even if a runtime compiles it solely as a grammar.
Windows target 4096 transcript tokens, bounded further by the remaining
context budget, and overlap by exactly two complete segments. An oversized
segment or a window unable to progress with that overlap fails explicitly;
no segment is split or omitted. Actual response usage and per-call timing
are recorded separately from planning reserves.

Each completion is strictly parsed into the step-2a `ExtractionResult`.
Invalid JSON, duplicate keys, non-finite numbers, wrong structure, unexpected
finish reasons, returned reasoning, and `finish_reason=length` fail the run
without retrying. A response at the 64-assertion decoding ceiling also fails
because completeness is uncertain. References outside the supplied window
fail. A later failed chunk leaves no run-level assertions or mappings; earlier
raw/parsed calls remain diagnostic data only. Reduced-schema rejection is
FAIL; stages not reached are NOT_CHECKED.

Merging uses kind, normalized concept, polarity, experiencer and overlapping
aligned character spans. Identical facts coalesce while retaining every
unique evidence item. Same-concept/span disagreements in kind, polarity,
experiencer or any other field retain both assertions and mark both
`merge_conflict`. Evidence unions exceeding the eight-span bound also retain
both records for review. Distinct non-overlapping occurrences remain distinct.
The 64-assertion limit is per completion, so the merged encounter is an
unbounded tuple of individually validated assertions, not a falsely truncated
single `ExtractionResult`. All original responses remain in the artifact.

## Lane 2 and review policy

The persisted lane key stays `evidence` for compatibility. The validation
report labels it **evidence and consistency**. The mapper always realigns
and runs consistency, so CLI callers and direct mapper callers have the same
gate. Original quote alignment/offsets remain available independently.

Hard FAILs are:

- A numeric value/dose token absent from the aligned evidence. Signs and
  decimal punctuation are significant; BP slash-separated numbers are
  checked separately. Attached units such as `500mg` retain the number.
  Numeric digits embedded in words such as SpO2 are not measurement values.
  Spelled-out numbers are not converted. The shared tokenizer now preserves
  leading decimals (`.5`, `-.5`) so they cannot silently match `5`.
- Non-null concept/allergen text absent as a contiguous, normalized whole-token
  phrase from any aligned quote, unless an explicit terminology synonym is
  present. The synonym rule permits only unambiguous exact same-code aliases
  from the local index, existing audited UK/US whole-name drug aliases, and
  the mapper's existing vital-name groups. No fuzzy candidates, inferred
  drug families, or new clinical synonym lists are used. Null fields stay
  mapper/review concerns. Paraphrases outside these aliases can still fail;
  the prompt asks for verbatim fields to limit that burden.

Review reasons (without turning lexical ambiguity into a hard FAIL) are
`polarity_conflicts_with_quote`, `negation_not_in_quote`,
`experiencer_cue_conflict`, `source_role_mismatch`, and `kind_role_mismatch`.
They inspect aligned quotes and actual cited segment roles. The shared
negation detector includes none, nil, negative, nothing and absent in
addition to the existing cues, contractions, and "ruled out". Family and
clinician-kind conflicts are conservative lexical checks, not a clinical
scope model. Multiple facts in a wide quote can trigger review even when the
negation or family cue concerns another fact. No polarity is silently fixed.

All consistency concerns conservatively force materiality, including hard
failures; merge conflicts do too. No review digest can waive a FAIL.
Consistency/merge conflicts on intentional ledger rows are `needs_review`,
not a clean `ledger_only` pertinent negative, and block final collection.
Clean ledger negatives retain the A4 exception. Unsupported numeric/concept
mutations in older mapper tests were changed to explicit safety expectations,
or given matching fictional evidence when testing positive mapping behavior.
Golden clinical FHIR content is unchanged; expected outcomes add an empty
consistency result.

## A3/A5 decisions and limits

Family history uses `patient_reported_condition` with `family_member`, so it
cannot acquire the intake patient's identity. Explicit resolution is distinct
from historical temporality. Separate evidence items may cite separate
segments; each item must quote exactly one segment. These follow step 2a/2b
and do not add onset, abatement, allergy type, codes, or new FHIR semantics.
There is no disagreement with `fhir-reference.md`.

Window size versus total context, per-call versus encounter assertion bounds,
the output cap, synonym equivalence, and conservative materiality are the
implementation choices described above. The checker does not prove full
clinical entailment or completeness. A quotation can be authentic while its
classification is wrong, which is why review and the critical-error probe
remain necessary. The prompt and deterministic checks are not an accuracy
claim or evidence that the default model should be replaced.

## Verification and handoff

All checks use `.venv/bin/` directly; no mise, uv, dependency installation,
model download, or git history operation was run. The `llm` pytest marker is
registered. Ordinary tests keep `--disable-socket`; the marked integration
probe permits only 127.0.0.1. It skips with actionable reasons for missing
model/runtime or blocked localhost bind. Present corrupt artifacts and
runtime/inference errors fail instead of being hidden by a skip.

The integration probe runs all 15 step-2a contrastive cases with the real
prompt, strictly checks every response, and tests all five critical-error
classes against the fictional fixture gold. It does not assert perfect
accuracy or an F1 threshold. The export invariant is stressed with passing
structural lanes and no review, so missing Java or an unrelated FHIR failure
cannot make it vacuously pass. Actual validator results are recorded separately
after unloading the model. The offline positive control proves a supported,
non-material heart rate can be final_exportable.

The integration attempt in this sandbox skipped because localhost bind is
blocked. `cache/llm/step-4.json` records that reason, the prompt estimate/hash,
and no fabricated case results. Claude should run:

```sh
.venv/bin/pytest -m llm -q -rs
```

That same ignored private report will then contain per-case raw responses,
parsed assertions, critical-error flags, gate states, timings, actual
validation, prompt token count and sampled server RSS. Follow-ups are real
Qwen accuracy/truncation observations, measured chat-template/token counts,
and memory measurement against the 16 GB laptop / 12 GB pipeline budget.
Gemma inference and the 40-encounter model-selection study remain later work;
metadata alone does not authorize a default replacement.

Final commands and summaries:

```text
.venv/bin/ruff check
All checks passed!

.venv/bin/ruff format --check
70 files already formatted

.venv/bin/pytest -q --tb=short -rs
Validator wall-clock time — goldens + anchors: 5.335s
Validator wall-clock time — broken resources: 5.065s
SKIPPED [1] tests/test_extract_llm.py:96: sandbox blocks localhost bind; run llm tests outside this sandbox
842 passed, 1 skipped, 1 warning in 11.59s

git diff --check
exit 0, no output
```

The warning is the pre-existing smoke test intentionally proving sockets are
blocked. During development, `.venv/bin/ruff format`, targeted pytest runs,
and the fast suite were also run. Initial test failures exposed changed
consistency expectations and the CLI's incorrect assumption of an
`Intake.load()` method; the implementation now uses the existing
`load_intake()` function. The final full suite includes the actual official
validator tests and the new CLI test.
