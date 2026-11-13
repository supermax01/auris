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

The active prompt is `src/auris/extract/prompts/extraction_v2.md`. Its exact UTF-8
SHA-256 is:

```text
a66c105f84af129589e5047c1eb3b4457a13b527cebee495030cb7f05cffa93e
```

It defines all nine kinds, nullable fields, polarity, experiencer,
temporality including resolved, source role, medication intent, and verbatim
segment evidence. Its fictional contrasts teach the same classifications using
clinical names and values absent from the repository's test data. Transcript
text is data rather than instructions.
Segments render as `[s3 | patient | 00:42.1–00:45.0] text`; embedded CR/LF
are escaped to preserve one line per segment. Prompt hash and rendering
are covered by a fixed snapshot/hash test.

V2 has 4,195 characters, **907 tokens measured offline with the pinned Qwen
GGUF's tokenizer**, and a **1,399-token conservative estimate**. Measurement
used the existing `llama-tokenize` 0.9.0 (build 12110, commit cd959bae9), default
special-token handling and no chat template. The same command measures the
unchanged v1 as 883 tokens, matching Claude's earlier server `/tokenize` count.
This is a prompt-only count, not a complete chat request's context usage:

```sh
llama-tokenize --offline -m cache/models/Qwen3-8B-Q4_K_M.gguf \
  -f src/auris/extract/prompts/extraction_v2.md --show-count --ids
```

The unchanged
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

## Step 4b: contamination finding and leakage guard

Claude's external Qwen3-8B run with v1 reported correct kind, polarity and
experiencer on all 15 contrastive cases, one temporality miss routed to review,
zero critical errors and approximately 6.5 GB sampled server RSS. **The clinical
result is contaminated and cannot support an accuracy claim.** Three fixture
sentences appeared verbatim in v1: "My mother has diabetes.", "I have diabetes."
and "I have no cough." Other examples reused the penicillin, amoxicillin,
pneumonia and blood-pressure test content. PLAN section 10 and A9 require
separation of teaching examples from the data being measured.

V1 remains byte-for-byte unchanged for reproducibility (SHA-256
`d87f75a09bf8718eb3f5ecab3789b62236a22949a990402e0100dedf1127be23`). V2 is the
default. Every contrast has been rewritten, as has the numeric example in the
field definitions. The rules are unchanged. New fictional clinical content is
sesame, migraine, doxycycline, eczema and dizziness; the new values are 75 and
37.3. These examples teach denial versus statement, family versus patient,
discussion versus order, ruled-out versus assessed, verbatim vital value/unit
text and a negated symptom. They are fictional utterances, not treatment advice
or new clinical rules. No fixture or expected clinical answer was changed.

`auris.extract.check_leakage(prompt, corpus, clinical_terms=...)` is an offline
API for the future evaluation harness. It scans the entire prompt, including
quoted examples and field definitions, against every supplied text and reports:

- Exact sentences after Unicode NFKC/case normalization, whitespace and
  punctuation normalization. Short sentences are included.
- Nonidentical sentences with at least four tokens on **both** sides and
  token-set Jaccard **>= 0.6**, inclusively. Stop words, negations and numbers
  remain in the comparison. Exact matches are reported only in the first group.
- Whole-token clinical content shared across prompt and corpus, using a small
  fixture-derived watchlist: amoxicillin, asthma, bronchitis, cough, diabetes,
  penicillin, pneumonia and rash. Callers can supply their own multiword terms.

Quoted examples are separated from speaker labels and explanations. Sentence
boundaries are newlines and `.?!;`, except decimal points between digits.
The implementation performs no stemming, clinical synonym matching or semantic
entailment. Passing this heuristic is necessary, not proof of no contamination;
new evaluation corpora need their own clinical watchlist and split discipline.
It does not detect model pretraining exposure. Empty/blank corpora are errors.

The report provides `passed`, `raise_if_failed()` and `metadata()`. A harness
must run the check on its full dev/test texts before inference, persist the
metadata, and call `raise_if_failed()` before starting the model. Metadata
records `leakage_check: pass` or `fail`, checker version, thresholds, prompt and
corpus hashes, text count, clinical-watchlist hash and hit counts. It contains
no transcript text; detailed findings are for private inspection only. Normal
clinical extraction does not claim to have checked an evaluation corpus.

The offline regression discovers all transcript objects recursively in every
JSON file under `tests/fixtures/`, including nested FHIR goldens, and fails on
any hit against the active prompt. Positive controls verify that v1 fails,
including all three reported exact sentences. Tests cover the inclusive
threshold, minimum lengths, quoted examples, decimals, whole-token clinical
names, corpus discovery, metadata and refusal before runtime startup.

The `llm` test is explicitly a **contrastive smoke test**, not an accuracy claim
or the A9 evaluator. Its report labels the fewer-than-25-case limit and records
the prompt version and leakage result even when prerequisites cause a skip.
Leakage fails the run before model startup and is recorded as a failure, not a
skip. Each case's extraction metadata also carries the passing check. V2 must
still be rerun with the real model by Claude outside this sandbox.

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
- Non-null allergen text absent as a contiguous, normalized whole-token
  phrase from any aligned quote, unless an explicit terminology synonym is
  present. The synonym rule permits only unambiguous exact same-code aliases
  from the local index, existing audited UK/US whole-name drug aliases, and
  the mapper's existing vital-name groups. No fuzzy candidates, inferred
  drug families, or new clinical synonym lists are used. Null fields stay
  mapper/review concerns. As amended by Step 9, absent concept text uses the
  same lexical/alias check but raises `concept_text_not_in_quote` for review
  instead of failing the lane. This permits clinician review of normalised
  speech without automatically accepting its clinical meaning.

Review reasons (without turning lexical ambiguity into a hard FAIL) are
`polarity_conflicts_with_quote`, `negation_not_in_quote`,
`experiencer_cue_conflict`, `source_role_mismatch`, `kind_role_mismatch`, and
`concept_text_not_in_quote` (Step 9).
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

The integration smoke test runs all 15 step-2a contrastive cases with the real
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

That same ignored private smoke-test report will then contain prompt version,
leakage-check metadata, per-case raw responses,
parsed assertions, critical-error flags, gate states, timings, actual
validation, prompt token count and sampled server RSS. Follow-ups are real
Qwen smoke-test/truncation observations, measured chat-template/token counts,
and memory measurement against the 16 GB laptop / 12 GB pipeline budget.
Gemma inference and the 40-encounter model-selection study remain later work;
metadata alone does not authorize a default replacement.

Original step-4 commands and summaries (before step 4b):

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

Step-4b verification (network disabled; official validator tests included):

```text
.venv/bin/ruff check
All checks passed!

.venv/bin/ruff format --check
72 files already formatted

.venv/bin/pytest -q --disable-socket --tb=short -rs
Validator wall-clock time — goldens + anchors: 9.269s
Validator wall-clock time — broken resources: 9.017s
SKIPPED [1] tests/test_extract_llm.py:107: sandbox blocks localhost bind; run llm tests outside this sandbox
873 passed, 1 skipped, 1 warning in 26.34s

git diff --check
exit 0, no output
```

The warning is the existing test that intentionally attempts a blocked socket.
The cached GGUF's SHA-256 was checked against the frozen configuration before
reporting the measured token count. No dependencies or FHIR rules changed.
