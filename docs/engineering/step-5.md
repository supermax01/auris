# Step 5: deterministic summary, text-in run and fictional demo

`auris run --transcript T.json --intake I.json --out DIR [--model ID]`
now runs extraction, evidence/consistency, mapping and the existing official
validator lanes, then writes `extraction.json`, `validation.json`,
`bundle.json` and `note.md`. The extractor is unloaded before validation.
All four artifacts use the private atomic writer: 0600 files in a 0700
output directory. Missing output parents are created at 0700. Existing
nonempty, shared or symlink output directories are refused before inference;
reruns cannot leave earlier success artifacts mixed with a new failure.

A completed run exits 0 even when assertions require review, validation
fails for a resource, or the validator is unavailable. In the unavailable
case its lanes remain NOT_CHECKED with reasons. Extraction failures exit 1
and retain only extraction/validation diagnostics, with no success note or
bundle. Input/schema errors do not print transcript values. No runtime path
fetches a model, code table or FHIR package.

## Collection and review semantics

The collection transport and step-3 gates are unchanged. A record must have
`final_exportable` state, current validation and any required review before
it can enter the bundle. A4 still blocks the entire clinical export when a
material assertion is unresolved or failing. Clean intentional ledger rows
retain their existing exception. A nonmaterial failed record is excluded
without blocking other eligible records. Intake anchors must pass the
existing checks before any extracted record is exported.

When the run is blocked, or anchors cannot be checked, Step 5 deliberately
writes an **anchors-only collection transport**, using the existing builder
with an empty assertion sequence. It never uses preview mode to include
unreviewed clinical resources. The unchanged supplied anchors remain visible
even if their own checks failed or were unavailable; this is not a claim
that they passed US Core. `validation.json` retains those states, and the
note and console say no extracted records were exported. This implements
the requested empty-export artifact without weakening the normal collection
gate. No transaction bundle or server submission is added.

`validation.json.export` records actual exported IDs, blocking assertion
IDs, whether anchors passed, and the same run-level summary used by the
note. Individual eligibility can therefore differ from actual bundle
membership when another assertion blocks the visit. Every mapped row also
contains its exact note presentation and export state. The report's original
lane labels/issues remain intact. The renderer does not approve, dismiss,
repair or recode assertions.

## Deterministic note

The renderer uses plain Python templates and typed presentation schemas.
It reads only checked MappingOutcomes, their ledger/evidence, the supplied
transcript's speaker/timing metadata and validator results. It makes no
model calls. Resources supply vital values, condition statuses, draft
medications and allergy text; ledger facts use aligned transcript quotes.
Fixed vital codings often omit display text, so their already-claimed
specific profile supplies the human-readable measurement name. No new
clinical code or synonym table is introduced.

S contains symptoms and allergy statements/denials; O contains vitals; A
contains assessed, refuted and patient-reported conditions, with family
history explicitly attributed to someone other than the patient; P contains
draft orders. Every fact line carries an honest label and `[mm:ss · role]`.
Times come from aligned word timings when available, otherwise segment
starts; the header explains approximation. Failed alignment uses the cited
segment's start where available; absent segments show `[--:-- · unknown]`.
Failed evidence/consistency is labelled an unconfirmed extraction rather
than transcript-supported. Quotes and diagnostic text are flattened and
escaped so they cannot inject extra Markdown sections or HTML.

The review block includes every item that is not individually exportable,
including clean ledger facts with an explanation that no record is emitted.
This makes review visibility broader than mandatory review. The discussion
block includes every ledger-backed outcome, including refused mappings.
Empty sections say `not captured`. Drug-family allergies use the existing
terminology family's classification and remain text with coding review.

There is no affirmative `validated` label at tier zero. The brief's required
heading/negative label `not validated` still appears. An affirmative label
requires all five lanes to pass and the item to be final-exportable. Tier
one is not implemented by this step; a synthetic all-lanes-PASS test checks
that label boundary. Passing tier-zero resources say `profile-checked ·
coding not checked`, with any outstanding review label alongside it.

The header includes the A8 research scope, model ID, active prompt version
and UTC run start timestamp (also persisted in extraction metadata). Matching
the supplied intake to the bundled fictional intake adds the demo banner
and explicitly enables the official validator's example-URL option. This
does not enable example URLs for arbitrary intake files. Rendering is
byte-deterministic for fixed outcomes, timestamp and metadata; separate
live runs correctly have different timestamps.

## Demo and setup

`auris demo [--model ID]` uses `examples/transcript.json` and the unchanged
`examples/intake.json`, writing to `./out/demo/`. The 24-turn fictional
clinician/patient script spans 4:10 with invented segment times, pending the
commissioned recording. Thirteen hand-written assertions cover every A3
row, a second vital, a denied symptom, family history, a continuing medicine
and a medicine explicitly not ordered. The script is labelled fictional,
contains no real identities, and is not treatment advice.

`tests/fixtures/demo/expected.json` specifies the hand-written extraction
and independently specified expected outcomes. Expected coding coverage is
that of the small existing offline terminology fixture, not the full cache.
The exact inspected `note.md` golden fixes the run timestamp and uses a
fake passing structural validator. It is a smoke/regression fixture, not
independent clinical gold or an accuracy claim. The ordinary CLI regression
replays the extraction through FakeBackend with real chunk planning,
parsing, merging, evidence, consistency, mapping and export gates. Positive
controls export both vitals without the demo's material blockers.

The demo passes the step-4b leakage guard with all segment texts and an
explicit demo clinical watchlist. The marked real-model test runs this
guard before starting the server and records its metadata. The prompt,
frozen config and existing clinical fixtures were not changed.

Help now lists setup/extract/run/demo/checklist. Missing unit sets point to
`auris setup fhir`; missing terminology to `auris setup terminology --fetch`;
validator unavailability to `auris setup fhir` followed by
`auris setup validator --fetch`; and a missing model to
`auris setup model --model ID --fetch`.

There was no model setup command to point to. The new `auris setup model`
verifies the frozen GGUF size (when pinned) and SHA-256, and checks that
`llama-server` is available. Only explicit `--fetch` downloads a missing
model from the candidate's pinned upstream revision. Downloads go through
a temporary file and are promoted only after verification; existing
corrupt artifacts are refused. It respects `AURIS_CACHE` like the run
command. It installs no runtime or language. The existing runtime must be
configured through PATH or `AURIS_LLAMA_SERVER`.

## Verification

Commands were run directly from `.venv/bin/`; no mise, uv, installation,
network setup, commit or push was run. Initial targeted runs caught a
misplaced CLI import, the as-yet-unwritten golden, and formatting issues;
these were fixed before the final suite. Golden inspection also caught
the absence of display names in fixed vital codings, corrected as above.

Final checks:

```text
.venv/bin/ruff check
All checks passed!

.venv/bin/ruff format --check
81 files already formatted

.venv/bin/pytest -m 'not llm' -q --disable-socket --tb=short -rs
Validator wall-clock time — goldens + anchors: 6.841s
Validator wall-clock time — broken resources: 6.424s
893 passed, 2 deselected, 1 warning in 15.21s
```

The warning is the existing test deliberately proving socket blocking.
Development also used `.venv/bin/ruff format`, `.venv/bin/ruff check --fix`,
targeted pytest runs, and a local Python script to write/inspect the
fictional fixtures. No dependency was added; the code remains Apache-2.0.
Model/runtime licences remain their existing pins (Qwen/Gemma Apache-2.0;
llama.cpp MIT). No terminology, model weights or new clinical codes ship
with this step. There is no disagreement with `fhir-reference.md`.

## Follow-ups and limits

Claude should run the real Qwen probe outside the sandbox:

```sh
.venv/bin/pytest tests/test_demo_llm.py -m llm -q -rs
```

It invokes the actual `auris demo` handler in a temporary working directory,
using the repository's prepared caches. `cache/llm/demo.json` records each
expected assertion beside the actual assertions sharing its evidence
segment, parsed field comparisons, mapping outcomes, raw extraction,
validation, whole-command wall time and sampled peak server RSS. Extra or
missing predictions remain visible. It is one mock consultation and a
low-confidence smoke test, not an accuracy or whole-pipeline memory claim.
Missing prerequisites produce an actionable skip; corrupt/runtime errors
fail. No real-model run or new real-model measurement was performed in
this step.

The original Step 5 retained the step-4 **1,024-token** output limit. Claude's
subsequent real Qwen demo failed safely on truncation: all 24 segments were
sent together, `usage.prompt_tokens` was 2,080 and `completion_tokens` hit
1,024. The planner had reserved 5,736 input tokens. Step 5b below replaces
that output-budget policy. The commissioned audio, review workflow/log,
transaction-server integration and whole-pipeline memory measurement remain
later work.

## Step 5b: measured budgets and adaptive splitting

The frozen configuration now specifies `max_output_tokens = 4096` and
`min_output_tokens = 1024`; `n_ctx` remains 8,192. For each window:

```text
max_tokens = min(max_output_tokens, n_ctx - input_tokens - 256)
```

The planner accepts a whole encounter when it leaves at least the output
floor plus headroom. Otherwise it makes the existing segment-boundary
windows, with the A5 ~4,096-token transcript target and two-segment overlap.
No completion is sent below the output floor. At the reported 2,080-token
input size the new cap would be 4,096; this arithmetic is not a new live
measurement.

For HttpBackend, `/apply-template` receives the same messages, schema
response format and thinking-off template kwargs as `/v1/chat/completions`.
Its rendered prompt is passed to `/tokenize` with `add_special=true` and
`parse_special=true`. The tested runtime's
[server implementation](https://raw.githubusercontent.com/ggml-org/llama.cpp/cd959bae9/tools/server/server-context.cpp)
uses the same chat parser for template application and chat completions,
and those two tokenization flags on the text completion path. The
[tokenization helper](https://raw.githubusercontent.com/ggml-org/llama.cpp/cd959bae9/tools/server/server-common.cpp)
routes text prompts through the same tokenizer. The schema is not separately
reserved when measuring: the server determines how it affects the rendered
prompt and decoding grammar.

A live server that cannot render/tokenize fails the run; it cannot silently
switch to estimation. Backends without rendered measurement, including the
ordinary FakeBackend, retain the conservative UTF-8/character estimate of
message contents plus schema, explicitly labelled `conservative_utf8_chars`.
Raw-text tokenizer availability alone is not rendered-input measurement.

Each call records its `reserved_input_tokens`, `token_count_method`, chosen
`max_tokens`, window ID, segment IDs, timing and usage. The
`input_token_comparison` records tokenizer count, `usage.prompt_tokens`,
their difference (`usage - tokenizer`), and `match`, `mismatch`,
`usage_unavailable` or `estimate_only`. Exact measurements must match usage;
a mismatch or missing/invalid usage fails closed without retry. Comparisons
are also collected under `metadata.input_token_comparisons`, including
truncated calls. Existing top-level metadata `prompt_tokens` still describes
the raw instruction text only; it is not the input budget.

On `finish_reason=length`, the generated body is discarded entirely, even
when it is complete, schema-valid JSON. No raw body or parsed assertions
from that attempt enter the artifact or merge. Usage, timing, finish reason
and an `output_discarded` marker remain. The window is split into balanced,
contiguous halves; each is measured again and receives its own budget.
Processing is deterministically left-first, recursively. Successful leaves
use the unchanged evidence-based A5 merger and conflict review rules.

Two-segment overlap is retained for parents of four or more segments. The
necessary minimum-size exception is explicit: three segments split into
two pairs with one-segment overlap, and two split into singletons with no
overlap. Keeping two overlapping segments there would repeat a parent
unchanged, preventing progress to the required one-segment minimum. Every
child is strictly smaller. Identical windows revisited through overlapping
branches reuse already completed work; they never issue the same inference
again. `metadata.splits` records parent/child IDs, their segment lists,
reason and actual overlap; `metadata.window_reuses` records those references.

A single segment that still truncates fails the entire run with an explicit
reason. Earlier successful leaves remain diagnostics only; run assertions
stay empty and mapping/export never starts. Other malformed responses and
transport failures retain their existing failure behavior. Existing CLI
handlers already return 1 for extraction failure; offline tests now exercise
both `auris run` and `auris demo` through recursive single-segment failure,
checking that only extraction/validation diagnostics exist.

Offline tests cover exact measurement versus estimation, transport template
parity, token-count verification, floor/ceiling arithmetic, planning before
inference, child budgets, discarded valid and invalid truncated JSON,
overlap/coverage/progress, deterministic recursive plans, reuse, successful
leaf merging and failure after a successful sibling. The two old mocked
chunk tests now use distinct windows (overlapping for the conflict case),
since identical windows are intentionally reused.

No dependency was added. No FHIR, clinical prompt, terminology, model or
licensing rule changed, and there is no disagreement with the FHIR reference.
The smaller-overlap minimum-size exception above is the only A5 interpretation
needed by this step. Claude must rerun the real demo outside the sandbox and
inspect the tokenizer/usage comparisons and split metadata; offline tests
do not establish real-model extraction quality or successful live completion.

Step 5b final checks (direct project-venv commands; no installs or live model):

```text
.venv/bin/ruff check
All checks passed!

.venv/bin/ruff format --check
83 files already formatted

.venv/bin/pytest -m 'not llm' -q --disable-socket --tb=short -rs
Validator wall-clock time — goldens + anchors: 7.766s
Validator wall-clock time — broken resources: 6.513s
978 passed, 2 deselected, 1 warning in 16.83s

git diff --check
(no output; exit 0)
```

The warning is the existing deliberate socket-blocking test. Development
also ran `.venv/bin/ruff format`, `.venv/bin/ruff check --fix`, and:

```text
.venv/bin/pytest tests/test_extract_backend.py tests/test_extract_budget.py tests/test_extract_chunking.py tests/test_extract_pipeline.py tests/test_run.py tests/test_config.py -q --disable-socket --tb=short
172 passed in 1.18s
```

That targeted result preceded the last three budget cases, which are included
in the final full-suite result. Initial checks found formatting issues and
the two identical-window test fixtures described above; both were corrected.
