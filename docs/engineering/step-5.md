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

The frozen step-4 output limit remains **1,024 tokens**. Thirteen assertions
may exhaust it when the full script fits the input context. The smoke test
preserves and fails on that truncation instead of accepting partial output.
Any output-budget/window-policy change needs the separate step-4/frozen-config
review; the fixture replay does not prove this dense visit completes on
real Qwen. The commissioned audio, review workflow/log, transaction-server
integration and whole-pipeline memory measurement remain later work.
