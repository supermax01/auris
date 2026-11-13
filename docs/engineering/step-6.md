# Steps 6a–6b: evaluation harness and analysable comparison

This step adds the versioned gold format, frozen-run evaluation CLI, independent
matching, statistical summaries, critical-error analysis, and a fictional
harness-check dataset. No prompt, model pin, terminology lexicon, mapper rule,
FHIR contract, dependency, or default model was changed. Step 6b adds the verified
Gemma reasoning-budget flag and scorer version 2 as described below. There is no disagreement
with `docs/fhir-reference.md`. No real model was run in the sandbox.

## Gold and scope

`src/auris/eval/gold.py` defines manifest schema version 1. One manifest contains
one split (`dev`, `test`, `harness-check`), a stable split ID, declared kinds,
a clinical leakage watchlist, and encounters with source, licence, fictional
status, transcript path/hash, supplied intake path/hash, annotation attribution,
and stand-off gold. Paths must resolve inside the manifest directory. Transcript
hashes use the existing domain-separated canonical transcript representation;
intake hashes cover file bytes. Evidence offsets are Unicode character indexes,
zero-based and end-exclusive; span SHA-256 covers the exact UTF-8 substring.
The loader verifies every hash and every evidence extent before inference.

Gold keeps the reduced-schema fields, adjudicated concept ID and acceptable
texts, optional codes, canonical numeric values/UCUM, explicit allergy-denial
scope, condition status, order authorization, and important out-of-scope facts.
The format rejects fields inappropriate for a kind and inconsistent critical
annotations. Annotation IDs are pseudonymous labels, not personal names/emails.
Independent annotation and agreement are recorded, never invented. Speaker/role
gold is the adjudicated transcript's role field; a companion can describe the
patient without becoming the experiencer.

The shipped 20 encounters, 120 segments and 60 assertions are **fictional
harness checks, NOT accuracy evidence**. The same party wrote transcript and
gold. All 20 have `fictional: true`, split `harness-check`, Apache-2.0 provenance,
and `harness-authored`, non-independent annotations. See
[`eval/harness-check/README.md`](../../eval/harness-check/README.md) for per-kind
counts and the encounter-to-critical-error map. The gold has no quoted evidence
and no invented standard clinical codes. All kinds have fewer than 25 examples.
Two further important facts are explicitly out of scope.

## Matching and statistics

The pure-Python Hungarian solver supports rectangular finite weight matrices,
including negative weights and deterministic ties. Six hundred seeded random
small matrices are checked against exhaustive assignment. Eligible-pair
cardinality dominates an evidence-overlap tie-breaker. Duplicated predictions
cannot consume the same gold twice. Strict and lenient assignments are optimized
independently.

The lenient key is resource class, experiencer, polarity, and concept. Three
condition kinds share `Condition`; the two allergy kinds share
`AllergyIntolerance`; symptoms and reported medications have separate named
ledger classes. Per-kind diagnostics additionally require the same kind.
Concepts match only through a gold canonical ID (when predicted coding identifies
it) or adjudicated acceptable text. No production clinical lexicon is imported
by matching or safety scoring. Normalization is NFKC, case folding, whitespace
and punctuation token normalization, preserving order, negations, numeric signs,
and decimals. It neither removes “the” nor invents synonyms: “medicine” and
“the same medicine” match only when explicitly adjudicated as acceptable.

Strict matches also require a nonempty evidence intersection in the same
segment, an identical system/code pair when both sides have codes, equal Decimal
values and canonical UCUM for vitals (ordered systolic/diastolic for BP), and
normalized dose, route, and frequency for orders. Scorer version 2 reports three
independently optimized assignments: `lenient`, `assertion_strict`, and
`record_strict` (the ambiguous version-1 `strict` key is removed).

Assertion-strict reads `value_text` and `unit_text` directly, independently of
whether the mapper built a resource or sent it to review. The small scorer-owned
table in `eval/normalization.py` recognizes numeric BP pairs (`126 over 78`,
`126/78`), decimal numbers, beats/breaths per minute, percent, Fahrenheit/Celsius,
pounds/kilograms, inches/centimetres, millimetres of mercury, and their canonical
UCUM spellings. It performs no unit conversion or default-unit inference.
Missing spoken units (including BP), unparseable values and missing aligned
evidence fail assertion-strict. The production mapper may supply a profile-fixed
unit under A3; that does not manufacture an extracted unit for this score.

Record-strict requires a built resource of the expected type for FHIR classes
and reads vital quantities from that frozen resource. It compares both BP
components in systolic/diastolic LOINC order, regardless of JSON order, and checks
each component's value, unit and UCUM system. Missing or duplicate components
fail. Ledger classes keep assertion-strict criteria. Both strict views retain
the A9 evidence, available-code, and extracted order dose/route/frequency checks;
record-strict is not a full semantic FHIR comparison, nor a validation or export
pass rate. The planned independent-FHIR annotation subset remains necessary.

Dose normalization uses an independent spelling table for mg/milligram(s),
mcg/microgram(s), g/gram(s), mL/millilitre(s)/milliliter(s), and unit(s), with
Decimal-normalized numbers. Strict order matching and the wrong-drug-or-dose
critical check share this scorer-only function. `500 mg` equals `500 milligrams`;
`50 mg` does not. No mg-to-g conversion, tablet inference, or route/frequency
synonyms are introduced. Other words remain literal. Re-scoring never calls the
mapper or its clinical lexicon.

Per-class and per-kind P/R/F1 include TP, FP, FN, gold N and N < 25 flags. Micro
pools counts; macro averages classes with either gold or predictions. Empty
precision/recall denominators produce 0 (counts remain visible), and wholly
absent classes are excluded from macro. Encounter-resampling percentile 95% CIs
cover micro/macro P/R/F1 and per-class F1. Default seed is 1729 with 2,000
replicates, frozen before scoring. Paired model-difference bootstrap resamples
the same encounters for both models. Source datasets are also reported separately
when a manifest combines sources. Public pretraining exposure remains unknown.

Declared fact coverage is gold in declared kinds divided by all gold plus
important out-of-scope facts. The fictional set's declared scope covers 60/62.
This is distinct from recall and export fraction. Lane reports include PASS,
FAIL and NOT_CHECKED counts, PASS/all and PASS/checked rates; these are
assertion-level lanes. Failed extraction encounters stay in gold recall
denominators, and encounter schema states/success rate are recorded separately.
Review burden is exactly `needs_review` items per encounter, not clinician
correction effort or elapsed review time. Other export states remain visible.

## Five critical errors

All typed frozen predictions participate in pre-review scoring regardless of
export/validation state. Each prediction can contribute at most one event per
category; categories can overlap. A second view includes only
`final_exportable` predictions without review. Reviewed predictions remain in
the pre-review view. Production runs in this step perform no human review.

Gold concepts, denial/status/order attributes and evidence identify errors:
allergy denial (including general NKA) made affirmative; wrong medication or
stated dose (orders and reported medicines); wrong known experiencer; an affirmed
new order without authorization; and a denied/refuted condition made affirmative.
An omitted dose is a strict-match miss rather than an invented dose. Unsupported
new orders count even if no medication occurs in gold. Evidence anchors separate
repeated mentions; gold concept association is the fallback when evidence is
missing. A failed quote's cited segment can localize safety analysis, but cannot
provide strict-match evidence. This is deterministic analysis of exhaustive gold,
not clinical entailment or a substitute for annotation review.

Reports show event counts and affected encounters for every category in both
views. Rates and exact one-sided 95% Clopper–Pearson upper bounds use affected
encounters / all encounters. This denominator avoids pretending multiple facts
from one visit are independent safety trials. Bounds use binomial-CDF bisection
with log-space summation; known-value tests distinguish one-sided 95% from a
two-sided interval's upper endpoint. At zero events, the exact bound remains
nonzero and `min(1, 3/N)` is shown as the rule-of-three approximation. Zero does
not establish safety, especially on 20 same-author fictional encounters.

## Commands, snapshots and locking

```sh
.venv/bin/auris eval run --dataset eval/harness-check/manifest.json \
  --model qwen3-8b-q4_k_m --model gemma-4-e4b-it-q4_0 --out out/eval/harness-v1
.venv/bin/auris eval score --run out/eval/harness-v1
.venv/bin/auris eval compare --run out/eval/harness-v1
```

`run` takes repeated model flags (defaulting to the pinned default), optional
`--seed`/`--bootstrap-samples`, and requires a new/empty private output directory.
Every candidate comes from frozen config with its own runtime flags and GGUF
hash. It starts one managed server per model, runs every encounter sequentially,
and unloads before mapping/Java validation. Models are also sequential. Existing
setup caches are prerequisites; no run or test downloads anything.

Gemma's per-model flags are now `--reasoning off --reasoning-budget 0`, based on
Claude's supplied hc-01 probe (zero reasoning characters, valid JSON, 403
completion tokens). Qwen retains `--reasoning off`: its template has not been
verified with the additional flag, and FakeBackend cannot establish equivalence.
The hard refusal of any reasoning output remains unchanged. Offline tests check
both config and managed-server command construction, not real model behavior.
Any failed encounter still makes `eval run` exit nonzero; stderr prints each
failing model's total and counts by failure reason. Those counts are stored in
`run.json` under `model_metadata.<model>.failure_reasons`.

The Step 4b leakage check scans the active prompt against every segment and the
manifest watchlist before any model startup. Pass or failure metadata is written
to `leakage.json`; a hit refuses the run. Passing a heuristic is not proof of
absence of contamination. No fixture or prompt is changed to improve a score.

The private run contains copied dataset/gold/intake, prompt text, frozen config,
full extraction calls/usage/token-budget/split metadata, mapping and validation
artifacts, typed scoring projections, environment details, implementation and
scorer hashes, and per-model wall time and sampled peak server RSS. Server RSS
covers the model lifetime across encounters; it is neither per-encounter nor
whole-pipeline/GPU memory. Failed encounters are retained with no accepted facts.
Truncated responses stay discarded by the existing extraction contract; invalid
JSON and non-promoted diagnostic completions are not interpreted as clinical
facts. Failure rates must be read alongside critical-error rates.

`score` validates frozen artifact hashes and supports inference-time scorer
versions 1 and 2, then writes deterministic `scores.json` and a digest receipt.
It records the active `scorer_version`/`scorer_sha256` alongside the original
`inference_scorer` version/hash, rather than refusing an intentional scorer
upgrade. Unknown versions are refused. Version-1 review reasons, original
quotes and built resources are recovered from hashed `extraction.json` mappings;
frozen predictions, config and `run.json` are never rewritten. Failure counts
for legacy runs are reconstructed in scored metadata, not backfilled on disk.
It needs no model,
terminology, validator, network, original dataset path or current frozen config.
`compare` verifies that receipt and requires scores from the active scorer
version/source hash (otherwise run `score` first). It writes deterministic `report.md` and
`report.json` with side-by-side metrics and paired CIs. Re-scoring, including an
explicitly authorized locked-test rerun, is byte-identical for the same scorer;
rerun audit timestamps
are deliberately outside the numeric report.

The shared append-only ledger is `$AURIS_CACHE/eval/test-ledger.jsonl` (default
`cache/eval/test-ledger.jsonl`), separate from run directories. POSIX file locking
serializes check-and-append. A test run reserves its split before inference;
failed attempts consume that reservation. The first score for that run is
allowed. A second run of that split or overlapping transcript hashes, and a
second score, require `--confirm-locked-test-rerun` and append its explicit
confirmation. Changing output directories or split names does not bypass the
content-overlap check. Dev and harness-check do not lock. Preserve the shared
ledger across cache migrations; deleting it removes its audit history. This is
an accidental-reuse guard, not tamper-resistant access control. Compare reads an
existing score without creating another test evaluation; if no score exists,
it performs the initial score under the same ledger guard.

A5 selection requires the development cohort of 17 PriMock57 + 23 ACI-Bench,
at least 0.03 micro lenient F1 gain, equal declared coverage, no increase in any
critical category in either view, and measured whole-pipeline laptop budget
compliance. Strict scores and paired delta CIs are always reported. Default is
labelled provisional when the baseline F1 or paired-difference CI half-width
exceeds 0.03. Current text runs only measure server RSS and text runtime, so
budget compliance is **unverified**, never inferred. The selection function can
check explicit whole-pipeline measurements, but this step has no acquisition
path for them and cannot authorize a real replacement. On harness-check data,
the verdict is always exactly `not applicable: fictional harness-check set`.

## Verification and follow-ups

All commands use the existing project venv; no dependency or language installs,
network setup, model inference, commits or pushes were performed. No dependency
was added. Existing Python dependencies and model licences are unchanged; this
code and authored fixtures are Apache-2.0.

Offline tests cover the assignment optimum, independent acceptable texts,
strict/lenient differences, hashes, fixture separation, hand-computed metrics,
exact bounds, deterministic bootstrap, all five safety categories in every
export state, source strata, every A5 gate, FakeBackend server lifetimes,
leakage refusal, immutable replay, private output permissions, failed encounters,
and shared test locking. Final command summaries are recorded below after checks.

Follow-ups for Claude/owner: run the actual Qwen/Gemma comparison outside the
sandbox; review diff, run checks and commit; choose independent annotators and
freeze clinical gold/splits; obtain the planned independent-FHIR subset and
whole-pipeline hardware measurements. The 40-encounter A5 selection study,
clinician correction-time measurements, clinical accuracy, and production model
replacement cannot be inferred from this harness-check set. No frozen config
or default candidate is changed automatically.

Step 6a final checks (direct project venv, network disabled):

```text
.venv/bin/ruff check
All checks passed!

.venv/bin/ruff format --check
99 files already formatted

.venv/bin/pytest tests/eval -q --disable-socket --tb=short
69 passed in 2.45s

.venv/bin/pytest -m 'not llm' -q --disable-socket --tb=short -rs
Validator wall-clock time — goldens + anchors: 6.347s
Validator wall-clock time — demo + display variants: 7.990s
Validator wall-clock time — broken resources: 10.458s
1148 passed, 2 deselected, 1 warning in 29.74s

git diff --check
(no output; exit 0)
```

The warning is the existing deliberate socket-blocking test. The two real-model
tests were deselected. The official validator regressions ran offline.
Development also used `ruff format`, `ruff check --fix`, targeted pytest runs,
read-only git status/diff, and direct `.venv/bin/python` fixture-assembly and
source-edit scripts. Initial checks found an incorrect helper import and
formatting/lint issues, all corrected. A full suite passed at 1,144 tests before
four final regression cases and their review-related fixes; the final 1,148
result above includes them. No setup, install, model, or git-history command ran.

Step 6a interpretations made explicit for review: resource-class matching has separate
ledger classes; macro uses classes with gold or predictions; safety confidence
bounds use encounter incidence; the selection metric is micro lenient F1;
the original lexical dose normalization did not infer unit or route synonyms
(Step 6b adds only the explicit dose-unit spelling table described above). These do not
change the plan's FHIR/export contract. Whole-pipeline budget evidence remains
unavailable and blocks real model replacement rather than being approximated
from sampled server RSS.

## Step 6b error analysis and replay verification

`scores.json` and `report.json` contain `models.<model>.error_analysis`, grouped
by error type. The Markdown report renders the same records as side-by-side gold
and prediction tables in its **Error analysis** section. Every critical event
gets a record in each applicable stage (`pre_review`, `exported_without_review`).
Every unmatched gold assertion and prediction gets a false-negative or
false-positive record for each of the three resource-class scoring views.
Every lenient-assigned pair failing either strict view gets that view's specific
failure reasons. Strict assignments remain independent, so a flagged lenient
pair may still rematch successfully in the strict assignment. These diagnostic
records are not extra events to add to the aggregate critical counts.

Records include encounter and fact IDs, gold concept IDs/acceptable texts,
classification fields, value/unit/dose/route/frequency, codes, exact evidence
quotes reconstructed from verified stand-off transcript spans, and prediction
export state, review reasons and all lanes. Gold-associated concept IDs are
labelled as associations: the extractor does not emit concept IDs or acceptable
texts. Original prediction quotes remain separately visible even when alignment
failed. For unmatched items, a related candidate may be displayed with concrete
mismatch reasons; it is not counted as a match. Missing counterparts are explicit.
Markdown escapes transcript markup and table delimiters.

The original `cache/eval-runs/harness-check-1/` was copied to
`cache/eval-runs/harness-check-1-step-6b-rescore/` and re-scored offline. No model,
mapper or validator was invoked during replay. The original comparison and all
frozen inputs remain unchanged. With scorer 2, Qwen micro F1 is 0.7869 lenient,
0.7541 assertion-strict, and 0.5902 record-strict. Its pre-review critical counts
are 2 wrong drug/dose, 1 wrong person, and 2 orders never given; the other types
and all exported-without-review types are zero. Item-level records explain each.
Gemma still has the original 18 reasoning refusals; its frozen run has not been
made into a valid new comparison by re-scoring. These are fictional harness
checks, not clinical accuracy results or model-selection evidence.

Step 6b adds no dependencies (licences unchanged) and makes no changes to prompts,
gold, production mapping, review/export policy, or the FHIR reference. The strict
view definitions and conservative missing-unit handling above are explicit scoring
interpretations for review. Follow-ups: Claude reviews the combined diff, reruns
both real models using the updated frozen config, runs checks, and commits.
Real-model behavior and independent-FHIR correctness remain outside this step's
offline verification.

Step 6b final checks (direct project venv, networking disabled for pytest):

```text
.venv/bin/ruff check
All checks passed!

.venv/bin/ruff format --check
103 files already formatted

.venv/bin/pytest tests/eval tests/test_config.py tests/test_extract_backend.py -q --disable-socket --tb=short
186 passed in 4.48s

.venv/bin/pytest -m 'not llm' -q --disable-socket --tb=short -rs
Validator wall-clock time — goldens + anchors: 6.532s
Validator wall-clock time — demo + display variants: 6.614s
Validator wall-clock time — broken resources: 9.019s
1212 passed, 2 deselected, 1 warning in 27.78s

.venv/bin/auris eval score --run cache/eval-runs/harness-check-1-step-6b-rescore
scored frozen predictions; wrote scores.json; fictional harness-check set, NOT accuracy evidence

.venv/bin/auris eval compare --run cache/eval-runs/harness-check-1-step-6b-rescore
not applicable: fictional harness-check set
wrote report.md and report.json

git diff --check
(no output; exit 0)
```

A direct `.venv/bin/python` replay/digest check also printed:
`Frozen-run replay: scores/report bytes identical; original frozen artifacts preserved; all critical diagnostics reconcile.`
The warning is the existing intentional socket-blocking test; the two real-model
tests were deselected. Development also used read-only `rg`, `cat`, `sed`, git
status/branch/diff, `apply_patch`, `ruff format`, targeted pytest, and a Python
copy of the frozen run. Early failures exposed two tests using version-1 strict
semantics, formatting, and a mocked Gemma file-size pin; all were corrected before
the final checks. No installs, dependency edits, setup, inference, commits,
pushes, or git-history changes were made.
