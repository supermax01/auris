# Step 8 — lean extraction profile

Step 8 outcome: **lean-v1 was not adopted** after the owner's real-model
harness-check run. `full-v0` remains the default. Step 8b's fixed ablation
contracts and run instructions are recorded below.
Step 8c adds opt-in compact decoding grammars; its pre-registered contracts,
local verification limits and four-model run command are recorded below.
Step 8d adds opt-in newline grammars and fixes literal key/enum spellings;
its pre-registered contracts and eight-entry run command are at the end.

Implemented on `slice-1`, 2026-11-12. Read PLAN, its binding A2–A6/A8
contracts, the FHIR reference and the Step 2b–6e engineering notes. There is
no separate Step 2a note in this checkout; its schema/tests are present.
No disagreement with the FHIR reference. No FHIR, terminology, review, evidence
guard, consistency rule, model pin or dependency changed.

## Contracts and selection

`extractor.extraction_profile` in `config/frozen.toml` defaults to `full-v0`:

| Profile | Schema | Prompt | Detail keys |
|---|---|---|---|
| `full-v0` | `1` | `extraction_v3` | Required, nullable; existing behavior |
| `lean-v1` | `2` | `extraction_v4` | Optional, nullable; omit when unsaid |

Schema v2 changes only the variants' `required` lists. It uses the same plain
`properties` plus `required` objects, disjoint kind variants and nullable
`anyOf` forms. Both exports pass `check_schema_subset`. There are no conditional
requirements, new grammar extensions or new nullable/classification semantics.
Kind, polarity, experiencer, temporality, source role, evidence and medication
intent for medication kinds remain required. Existing null/unknown/unclear
abstentions remain available. `concept_text` stays required: the fact's name is
needed for safe mapping/merging and missing names should remain explicit.

`extraction_json_schema("1"|"2")` and
`write_extraction_json_schema(path, schema_version="1"|"2")` export either
version. The v1 schema is byte-equivalent to the branch baseline, with sorted
JSON SHA-256 `88ddd54be2af2da644fdbd9b352fe7c4b201e11211de2207befacc001ce69bff`.
V2 parsing fills only omitted detail keys belonging to the selected kind with
null, then uses the unchanged pydantic v1-contract models. Missing classification
keys still fail. Canonical assertions, mapping and scoring therefore treat
omitted details exactly like explicit null. Original response text stays in
the private call artifact; the canonical assertion dump includes nulls.

The CLI accepts `--extraction-profile full-v0|lean-v1` on extract/run/demo.
Evaluation accepts repeated flags and runs the Cartesian product of selected
models and profiles, sequentially, unloading before each mapping/validator stage:

```sh
.venv/bin/auris eval run --dataset eval/harness-check/manifest.json \
  --model qwen3-8b-q4_k_m \
  --extraction-profile full-v0 --extraction-profile lean-v1 \
  --out cache/eval-runs/step-8-harness
.venv/bin/auris eval score --run cache/eval-runs/step-8-harness
.venv/bin/auris eval compare --run cache/eval-runs/step-8-harness
```

New run identities and directories are `MODEL@PROFILE`. Each selected prompt is
audited before any inference and frozen in `prompt-PROFILE.md`. The first is
also retained as `prompt.md`. Existing unprofiled runs remain loadable without
adding profile metadata during replay. Profile comparisons report token savings,
lenient F1 delta, critical-count changes and evidence failure rates/reasons.
The 35% token and -0.02 F1 targets have explicit results; material evidence-failure
rise remains a review judgment because no numeric threshold was supplied.
Item diagnostics still need review for new critical errors: equal aggregate
counts alone cannot prove that the same errors occurred. Profile comparisons
cannot trigger the A5 larger-model replacement gate for the same model.

## Prompt, whitespace and leakage

V1–v3 prompt files are byte-identical. V4 changes only evidence-span length,
omission of unsaid detail fields and the compact single-line JSON instruction.
It requires the shortest exact span with every meaning-changing word, including
negation, family/other-person and time context. The negation guard in
`evidence.py` is unchanged, including its conservative same-sentence check.
Compact and pretty-printed JSON both parse; malformed JSON, duplicates, nonfinite
numbers, returned reasoning, truncation and saturation retain their refusals.

V4 SHA-256:
`3a475634e959f5da2ac5b1ba6c622bf211fbd89777fd7bf86b2de4b0826e23e4`.
Length: 4,428 characters; **952 tokens** with the existing pinned Qwen GGUF's
offline tokenizer (v3: 909); conservative estimate: 1,479 tokens. These are
raw prompt counts, not rendered chat/schema input budgets.

```sh
llama-tokenize --offline -m cache/models/Qwen3-8B-Q4_K_M.gguf \
  -f src/auris/extract/prompts/extraction_v4.md --show-count --ids
```

Inspected the installed llama.cpp 0.9.0 README, file inventory and server help.
The README links the upstream grammar guide but the installed package omits
that guide, the JSON-schema converter and the server README/source. The local
interface exposes grammar/schema flags but no documented portable whitespace
option for `response_format`. No portable option was verified, so no custom
request field or grammar is added. V2 keeps plain `properties`/`required`,
and compactness relies on the prompt. Each accepted call records whether its
actual output was single-line/compact and the whitespace count outside strings;
spaces and escaped newlines inside clinical text are preserved.

The existing leakage rules and thresholds are unchanged:

| Corpus | Texts | Exact hits | Near duplicates | Guard |
|---|---:|---:|---:|---|
| Complete verified PriMock57 | 57 | 0 | 0 | PASS |
| Complete eligible verified ACI pool | 195 | 0 | 0 | PASS |
| Fictional harness-check | 20 | 0 | 0 | PASS |
| Recursive fixture transcripts, demo and annotation-guide examples | all discovered | 0 | 0 | PASS |

Public clinical-watchword warnings are also zero. Full-corpus audits used
`auris eval leakage-report --dataset primock57|aci-bench|eval/harness-check/manifest.json
--prompt-version extraction_v4`, with separate invocations. Private diagnostics
are under `cache/eval/leakage-reports/*-extraction_v4.json`; no source transcripts
or example text were copied into committed artifacts. Corpus audit reads are
lexical checks only, with no held-out extraction or evaluation.

## Measurements and serialization replay

Extraction metadata records profile/schema/prompt identity, output tokens per
merged assertion, quote character and lexical-word count/min/max/mean/median,
and evidence-failure counts. Token cost includes **all attempts**, including
discarded/truncated generation and overlap. Missing completion usage makes
the total/ratio unknown; it is never counted as zero. Failed encounters remain
in the evaluation denominators. Before mapping, evidence statistics describe
quote alignment; after mapping they include hard consistency failures. Reasons
are counted once per assertion, with separate alignment quote-failure counts.
`omitted_negation_context` is an explicit reason, so fresh v4 model runs can
measure its frequency without weakening the guard.

The fictional shortened-quote regression measures one unsafe quote as 1/1
omitted-negation failures and the context-preserving quote as 0/1. This proves
the gate remains strict, not the rate on actual v4 generations. That rate,
quality and acceptance targets require Claude's fresh model runs.

The offline helper re-serializes a recorded successful v3 response, omitting
only null optional details and compacting JSON. It sends both forms through
FakeBackend and their versioned parsers, requiring identical canonical facts.
It deliberately retains all quotes and drops obsolete counterfactual usage.

```sh
.venv/bin/python -m auris.extract.lean_replay \
  cache/primock-dev/day1_consultation02/extraction.json \
  --model cache/models/Qwen3-8B-Q4_K_M.gguf
```

The recorded file has SHA-256
`1dd18cfd741c7ed20eae3fc4644bf70af0e460b1d1adfc66594d5144f8537d4c`.
First retained completion, zero-based call 1: **24 assertions**, **2,851 → 1,956
tokens**, **118.79 → 81.50 tokens/assertion**, saving **31.39%**. Counts use
`llama-tokenize --offline --no-bos` on both bodies. This measures only null-key
and formatting savings on one successful call, excluding discarded calls and
any new shortening. It is below the 35% target and is neither a fresh-model
result nor evidence of unchanged F1/safety. No quotes were shortened by a
heuristic and no clinical facts were created for this estimate.

## Replay and verification

The unprofiled schema-v1 export and prior prompt hashes are pinned in tests.
Version-1 run replay tests verify frozen artifacts stay unchanged, no profile
defaults are backfilled, and repeated scores/reports are byte-identical.
Additionally, replayed a private copy of `harness-check-3` through the original
HEAD run/report implementations and the Step 8 implementations, holding scorer
identity fixed to isolate behavior: `scores.json`, `report.json` and `report.md`
were all byte-identical. Production continues to record the actual current
scorer source hash. Originals were not modified.

A direct comparison with the older cached JSON also exposed pre-existing replay
drift: the current annotation model adds `guidelines_sha256: null`. Both HEAD
and Step 8 produce it; cached Markdown is already identical at fixed scorer
identity. Resolving that historical serialization detail is a follow-up, not a
Step 8 scoring or annotation change.

Final checks and file inventory follow below. Development used direct venv
Python, targeted pytest, ruff format/check, local offline tokenizer/server help,
read-only git status/branch/show/diff and source/document reads. Initial failures
were a test response missing measured prompt usage and the legacy top-level
leakage-metadata shape; both were corrected. No mise/uv command, installation,
network request, real-model generation, commit or push was run.

Dependencies added: **none**. Auris stays Apache-2.0; existing llama.cpp is MIT.
No dependency manifests or lockfiles were edited. Follow-ups: Claude's model runs on
harness-check and PriMock57 dev, F1/critical-event/evidence-rate review against
the stated targets, and review/commit. No default-profile adoption is claimed.

```text
.venv/bin/ruff check
All checks passed!

.venv/bin/ruff format --check
141 files already formatted

.venv/bin/pytest -m 'not llm' -q --disable-socket --tb=short -rs
Validator wall-clock time — goldens + anchors: 7.555s
Validator wall-clock time — demo + display variants: 9.946s
Validator wall-clock time — broken resources: 8.722s
1483 passed, 2 deselected, 1 warning in 39.54s

git diff --check
(no output; exit 0)
```

The warning is the existing intentional blocked-socket test. Two real-model
tests were deselected. The final targeted profile/evaluation/locking check before
the last CLI additions passed 63 tests in 4.50s. Earlier full runs included one
leakage-shape failure, followed by a passing 1,480-test run; the final 1,483-test
run above includes every correction and added CLI/profile audit regression.
Offline tokenizer output was `Total number of tokens: 952`. The replay command
printed `full_output_tokens: 2851`, `lean_output_tokens: 1956`,
`saving_fraction: 0.3139249386180287`, `canonical_assertions_identical: true`.
All three leakage-report commands exited 0 without sentence hits; the resulting
JSON explicitly reports PASS. Original-HEAD versus Step 8 replay printed
`HEAD_vs_step8_byte_identical= True` for scores.json, report.json and report.md.

## Files created or changed

- `config/frozen.toml`: explicit full-profile default and lean opt-in comment.
- `src/auris/config.py`: validated extraction-profile setting with legacy default.
- `src/auris/assertions.py`: versioned schema export and optional-detail normalization.
- `src/auris/cli.py`: extract/run/demo profile overrides.
- `src/auris/extract/profiles.py`: profile-to-schema/prompt contracts.
- `src/auris/extract/prompts/extraction_v4.md`: shorter evidence, optional details, compact output.
- `src/auris/extract/prompt.py`: profile-aware immutable prompt loading/hash/rendering.
- `src/auris/extract/tokens.py`: retain profile in the input-counting context.
- `src/auris/extract/chunking.py`: measure the selected profile's actual prompt.
- `src/auris/extract/pipeline.py`: profile parsing, metadata and measurement plumbing.
- `src/auris/extract/measurements.py`: token cost, quote lengths, whitespace and lane reasons.
- `src/auris/extract/lean_replay.py`: offline recorded-response FakeBackend cost estimate.
- `src/auris/eval/runs.py`: per-profile audits, snapshots, identities and encounter measurements.
- `src/auris/eval/cli.py`: repeated evaluation profiles and v4 leakage-report selection.
- `src/auris/eval/leakage_report.py`: accept v4 without changing the guard.
- `src/auris/eval/profiles.py`: aggregate within-model cost/quality/evidence comparisons.
- `src/auris/eval/reports.py`: display profile comparisons while retaining legacy report behavior.
- `tests/test_extract_profiles.py`: schema/parser/mapping/projection, prompt, guard and cost regressions.
- `tests/eval/test_runs.py`: CLI profiles, per-profile audit refusal, frozen comparison and v1 replay.
- `docs/engineering/step-8.md`: implementation, measurements, limits and handoff.

Generated outputs are only the three gitignored private v4 leakage audits.
Temporary legacy replay copies were removed; existing frozen runs and PriMock
responses were read without modification. Deviations: concept text deliberately
remains required as allowed by this step; no automatic material-rise threshold
or unverified server whitespace knob was invented. The historical null annotation
hash drift and fresh inference remain the follow-ups described above.

## Step 8b — predefined ablations

Implemented on `slice-1`, 2026-11-13. Re-read PLAN and the FHIR reference;
there is no disagreement with either. The factors below were specified before
fresh ablation inference. No model inference or prompt tuning was performed.
The first three profiles isolate individual lean factors against `full-v0`;
`abl-order` compares the complete lean contract against `lean-v1`, changing
only the assertion-property order as requested.

| Profile | Schema | Prompt version | Property order | Change |
|---|---|---|---|---|
| `full-v0` | `1` | `extraction_v3` | Canonical | Unchanged control |
| `lean-v1` | `2` | `extraction_v4` | Canonical | Unchanged lean control |
| `abl-optional` | `2` | `extraction_abl_optional_v1` | Canonical | V3 plus one line: `Omit fields that were not said.` |
| `abl-quotes` | `1` | `extraction_abl_quotes_v1` | Canonical | V3 with only its evidence bullet replaced by V4's shortest exact span instruction |
| `abl-compact` | `1` | `extraction_abl_compact_v1` | Canonical | V3 plus one line: `Output compact JSON on a single line.` |
| `abl-order` | `2` | `extraction_v4` | Evidence last | Detail/classification fields precede evidence in every assertion |

The optional and compact lines are appended after V3's existing text; no other
wording or examples change. In particular the optional ablation retains V3's
existing null instructions, while schema V2's required classifications remain
enforced. `abl-optional` permits pretty JSON. No profile adds a whitespace
grammar or changes the parser, mapper, scorer, evidence guard or defaults.

Canonical property order is `kind`, `concept_text`, `polarity`, `experiencer`,
`temporality`, `source_speaker_role`, `evidence`, followed by each kind's
existing detail/intent fields. The order ablation moves only `evidence` to the
end of each `properties` object, preserving the other fields' relative order,
nullable types and `required` lists. This order also survives the schema file
export. Both schema versions continue to pass `check_schema_subset`.

New versioned prompt SHA-256 values (pinned in offline tests):

| Prompt | SHA-256 |
|---|---|
| `extraction_abl_optional_v1` | `0804045f2674259d20d6c9b3a73888f8ddd2780a3eb9ba02ae172478dd093600` |
| `extraction_abl_quotes_v1` | `fcb8438d00ae90cc7841beada9ae67314772d7ed572e44d32461250a8cb40521` |
| `extraction_abl_compact_v1` | `657b885e92b4052b6189f7917da6fa4559ea0d8e9fed37a981b029c04f86350f` |

Every new extraction and evaluation model entry records `extraction_profile`,
`schema_version`, `prompt_version`, `prompt_sha256`, `schema_property_order`,
and `assertion_property_order` (the exact ordered property names for each kind).
The six schema/prompt/order identities are distinct, including the two profiles
that deliberately share V4. Existing frozen runs replay without backfilling
these fields. Original full/lean schema exports, including property order, and
V1–V4 prompt bytes are unchanged.

The new prompts each passed the unchanged leakage guard against all verified
PriMock57 transcripts (57), the eligible verified ACI pool (195), the complete
fictional harness (20), recursive fixture transcripts, the demo, and annotation
guide examples. The nine full-corpus/manifest audits report PASS with no
sentence hits; private reports are under ignored `cache/eval/leakage-reports/`.
Audits only read text for lexical overlap; they do not perform extraction or
reserve held-out evaluation.

Claude should run the following exact two-model × six-profile grid, producing
twelve model/profile entries (all work remains sequential):

```sh
.venv/bin/auris eval run \
  --dataset eval/harness-check/manifest.json \
  --model bonsai-2-27b-q2_0 \
  --model gemma-4-e4b-it-q4_0 \
  --extraction-profile full-v0 \
  --extraction-profile lean-v1 \
  --extraction-profile abl-optional \
  --extraction-profile abl-quotes \
  --extraction-profile abl-compact \
  --extraction-profile abl-order \
  --out cache/eval-runs/step-8b-ablation
.venv/bin/auris eval early-stop --run cache/eval-runs/step-8b-ablation
.venv/bin/auris eval score --run cache/eval-runs/step-8b-ablation
.venv/bin/auris eval compare --run cache/eval-runs/step-8b-ablation
```

`early-stop` writes deterministic, quote-free JSON to stdout and does not write
or rescore any run files. It first verifies the frozen artifacts through the
existing replay loader. Each model/profile entry contains assertion counts per
visit and their count/total/min/max/mean/median, the number and share of visits
with exactly one assertion **among reference visits with at least three**, total
completion usage and per-visit statistics, and finish-reason counts. Per-visit
rows include the reference count, failure indicator, completion tokens, call
count and finish reasons. These are diagnostic counts, not proof of premature
termination or a new quality metric.

Failed visits remain in the assertion/eligible-reference denominators. Token
and finish counts include discarded/truncated attempts and overlap. A visit's
tokens are unknown when any call lacks valid usage or no call was made; the
aggregate total then stays null. Token statistics and `known_total` cover only
visits with complete usage; unknown visits and known-usage calls are counted
explicitly.
Missing finish reasons appear as `unknown`; no-call visits have no finish.
Legacy profile identities remain null when unrecorded.

Read-only analysis of the existing `harness-check-5-profiles` control run gave:

| Model/profile | Assertions/visit | Exactly one / eligible reference visits | Completion tokens | Finish reasons |
|---|---:|---:|---:|---|
| Bonsai 27B / full-v0 | 3.10 | 0/18 (0%) | 8,309 | stop: 20 |
| Bonsai 27B / lean-v1 | 2.30 | 6/18 (33.3%) | 3,332 | stop: 20 |
| Gemma E4B / full-v0 | 3.05 | 0/18 (0%) | 8,349 | stop: 20 |
| Gemma E4B / lean-v1 | 2.40 | 5/18 (27.8%) | 4,820 | stop: 20 |

The denominator is 18 because two harness visits have fewer than three
reference assertions. These are the owner's existing control outputs, not fresh
ablation results, and did not inform any further prompt changes.

Offline coverage includes all six schema exports and their exact property
order, pinned prompt hashes and predefined text differences, omitted-optionals
versus null through parsing/mapping/scoring projections, distinct identities,
leakage checks, CLI flags, six-profile FakeBackend evaluation and replay,
read-only/hash-verified analysis, and hand-built failed/missing-usage/legacy/
discarded-call fixtures. Full-v0/lean-v1 replay regression tests remain in place.
No dependencies or dependency manifests changed. Follow-up: Claude runs the
grid, reviews the four ablation effects and commits after review; no adoption
decision or default change is made here.

Step 8b final verification:

```text
.venv/bin/ruff check
All checks passed!

.venv/bin/ruff format --check
144 files already formatted

.venv/bin/pytest tests/test_extract_profiles.py tests/eval/test_early_stop.py tests/eval/test_runs.py -q --disable-socket --tb=short
160 passed in 14.69s

.venv/bin/pytest -m 'not llm' -q --disable-socket --tb=short -rs
1583 passed, 2 deselected, 1 warning in 40.19s

git diff --check
(no output; exit 0)
```

The warning is the existing intentional blocked-socket test. Initial new test
expectations were corrected for the harness's 18 eligible visits and the CLI's
existing error-return behavior; the passing results above include both fixes.
Schema V1 and V2 exports were also compared directly against HEAD's exporter:
their serialized bytes and property orders are identical. On a temporary copy
of `harness-check-5-profiles`, HEAD's run implementation and Step 8b's produced
byte-identical `scores.json`, `report.json` and `report.md` with scorer identity
held fixed to isolate replay behavior. All frozen inputs/predictions remained
unchanged and the temporary copy was removed. Production records the actual
current scorer source hash. No install, dependency edit, network operation,
fresh inference, commit or push was performed.

## Step 8c — pre-registered compact decoding grammar

Implemented on `slice-1`, 2026-11-13, before fresh grammar-profile inference.
Re-read PLAN (including Appendix A) and the FHIR reference; no disagreement
with either. `full-v0` remains the default. No assertion schema, prompt file,
model pin, parser, mapper, evidence guard, scorer or review rule changed.

The owner's Step 8b ablation found that the compact-output instruction reduced
recall, while compact formatting provided most of the speed benefit. Step 8c
tests the hypothesis that a decoding grammar can provide that formatting
without the instruction. This implementation makes no recall/speed claim.

| Profile | Schema | Prompt bytes/version | Decoding |
|---|---|---|---|
| `grammar-compact` | `1`, canonical order | Unchanged V3 / `extraction_v3` | `compact-gbnf-v1` |
| `grammar-compact-quotes` | `1`, canonical order | Unchanged short-quote-only ablation / `extraction_abl_quotes_v1` | `compact-gbnf-v1` |

The quotes profile reuses V4's evidence instruction only, exactly as frozen in
Step 8b's quote ablation. Neither profile contains the compact-output or
optional-detail instruction. The prompt SHA-256 values remain respectively
`0db6b464e45c02bfe5f16ee9f392f4b8456ecb3ceabcef71611fbdabd31ab581`
and `fcb8438d00ae90cc7841beada9ae67314772d7ed572e44d32461250a8cb40521`.
The existing profile leakage tests now also audit both new identities against
recursive fictional fixtures, the demo, harness-check and annotation examples.
No changed prompt text exists; these exact bytes already passed the full
public-corpus audits recorded above. Evaluation also audits selected profiles
before inference through its existing leakage gate.

### Converter and transport

`auris.extract.grammar.compact_grammar(schema)` deterministically converts the
exported schema, preserving property insertion order and nullable/required
semantics. It accepts closed fixed-property objects, disjoint singleton-kind
variants, string enums, strings, nullable `anyOf`, and bounded arrays. It refuses
unsupported validation keywords instead of ignoring them. Optional members
use suffix states that track whether a preceding member exists; this handles
leading, middle and trailing omissions without stray commas or exponential
enumeration. Every array enforces both its minimum and maximum length.

`ws ::= ""` is empty everywhere outside JSON strings, including before the
root and after its closing brace. String values retain spaces and correct
JSON escapes. Strings accept Unicode scalars, escaped controls and paired
UTF-16 surrogate escapes; raw controls and unpaired surrogates are rejected.
String length counts decoded scalars. Fixed keys and enum strings also accept
equivalent Unicode escapes, including mixed-case hexadecimal digits.

The canonical schema-v1 grammar is 6,620 UTF-8 bytes, with SHA-256
`54fcd41989c7508fd1251aa536886fdf157ac365b7d424871dfe1d8621a87c1e`.
Both new profiles record this hash and `grammar_version` alongside their
existing profile/schema/prompt/order identity in extraction and evaluation
metadata. `decoding.json_whitespace` is `grammar_compact_json`. A grammar
change requires a version/hash review before comparing new runs.

Only the new profiles pass a top-level `grammar` string to the local chat
completion request, omitting `response_format`. `/apply-template` receives
the identical template-affecting input, so measured input budgeting does not
accidentally retain the former schema request. Grammar use does not modify the
parser or post-generation safety checks. The six previous profiles still send
their original JSON-schema requests. Tests pin request byte hashes captured
by executing the committed Step 8b backend with fixed inputs, covering all six
profiles, rather than merely comparing parsed request dictionaries. Existing
profile metadata does not gain grammar fields, and frozen replay stays intact.

### What was verified locally

No network was used. The installed `llama-server --version` reports
`0.9.0 (build 12110, commit cd959bae9)`. Its `--help` documents `--grammar`,
`--grammar-file`, `--json-schema` and `--json-schema-file`. The installed README
links `grammars/README.md`, but that guide, sample `.gbnf` files, the schema
converter and server source/README are absent from this package.

The installed server implementation library's compiled parameter descriptions
explicitly name the JSON request's `grammar` string and `json_schema` fields.
Its chat adapter includes `Cannot use both json_schema and grammar` and
`Cannot use custom grammar constraints with tools.` Auris sends only grammar
and does not use tools. This verifies the field from the installed build;
help alone does not document the HTTP request body.

The installed public `llama.h` exposes `llama_sampler_init_grammar`, including
the production-string and root-symbol arguments. A one-off standard-library
`ctypes` probe called it with a null vocabulary (no model load). The native
parser accepted generated grammars for both schema versions and both property
orders. This checked empty productions, rule references, character classes,
Unicode/hex escapes, groups, alternatives, `?` and bounded `{min,max}` /
`{min,}` repetition against the actual build. A deliberately malformed grammar
returned null with a parse error, confirming the probe exercises parsing.
These are local inspection/probe steps, not test dependencies.

Not verified here: a live HTTP grammar request, native token-by-token decoding,
fresh model completeness/quality, or runtime speed. Claude's run below supplies
those checks. Tests require neither this native installation nor model weights.

### Equivalence scope and offline tests

An independent test helper reads the emitted GBNF text into a regex automaton;
it does not read the schema or call converter internals. Seeded generation
(`810803`) exercises both schemas and both property orders, 1,584 valid
documents (single-kind and mixed/empty lists), and thousands of invalid
mutations. It covers all nine kinds, enums/nulls, optional-detail combinations,
missing/extra/wrong-type fields, invalid variants, evidence bounds 0/1/8/9,
assertion bounds 0/1/63/64/65, Unicode, JSON escapes, malformed strings,
duplicates, invalid JSON and whitespace outside strings. Additional small
schemas exhaust all optional-key combinations, including empty objects, and
check one-scalar string bounds and zero-item arrays. Determinism and schema
non-mutation are pinned. Transport/pipeline tests verify both measurement and
inference constraint selection, usage agreement and grammar/profile metadata.
Existing tests exercise leakage and frozen evaluation replay for all profiles.

There are necessary qualifications to literal grammar-only/parser equivalence:
the requested generation order is stricter than JSON object order, and the
existing parser has two checks absent from the exported JSON Schema:

- Exactly 64 assertions is schema-valid but fails `parse_completion`'s
  completeness guard. The grammar must allow 64 to preserve schema bounds;
  65 is rejected by both. A 64-item completion still fails the full path.
- Nonempty all-whitespace evidence satisfies schema `minLength=1`, but fails
  the pydantic nonblank validator. The grammar permits it and the full path
  rejects it, preserving the existing rule.

Thus grammar-only equivalence holds for schema-ordered documents modulo
inter-token whitespace, subject to those explicitly tested post-schema
refusals. The complete grammar-plus-parser path retains the same acceptance
as the parser for schema-ordered documents. Exact grammar-only equivalence to
all parser checks cannot simultaneously preserve the unchanged exported
schema. No guard was weakened or bound silently reduced to claim otherwise.

### Linux path

XGrammar JSON-schema compilation with free whitespace disabled is the proposed
Linux equivalent: **to verify on the Linux profile**, including support in its
pinned version, the exact API/option and equivalent behavior. No local
XGrammar/vLLM package or documentation was found, so this capability/API is not
claimed as locally verified. No Linux code or unverified request option is
added in this step.

### Claude's run

Run this four-model × three-profile grid (12 entries), sequentially, using
already prepared model/terminology/validator caches and a fresh output directory:

```sh
.venv/bin/auris eval run \
  --dataset eval/harness-check/manifest.json \
  --model qwen3-8b-q4_k_m \
  --model bonsai-2-27b-q2_0 \
  --model bonsai-2-9b-q2_0 \
  --model gemma-4-e4b-it-q4_0 \
  --extraction-profile full-v0 \
  --extraction-profile grammar-compact \
  --extraction-profile grammar-compact-quotes \
  --out cache/eval-runs/step-8c-grammar
.venv/bin/auris eval early-stop --run cache/eval-runs/step-8c-grammar
.venv/bin/auris eval score --run cache/eval-runs/step-8c-grammar
.venv/bin/auris eval compare --run cache/eval-runs/step-8c-grammar
```

Inspect recall/F1, pre-review critical errors, evidence failures, assertion
counts and early-stop share, tokens/assertion, completion time and actual
outside-string whitespace by model against `full-v0`. Existing aggregate
profile comparison is specific to full/lean; the grammar grid remains in
per-profile model results and the generic early-stop report. No scorer/report
redesign is part of Step 8c. The harness is fictional and is not clinical
accuracy evidence. No profile adoption is pre-authorized by this implementation.

Dependencies added: **none**. No manifest/lockfile edits; Auris remains
Apache-2.0, existing llama.cpp is MIT. No installation, mise/uv invocation,
network operation, real-model inference, history change, commit or push.
Follow-ups: Claude's live grid/review/commit and Linux whitespace verification.

Final offline checks:

```text
.venv/bin/ruff check
All checks passed!

.venv/bin/ruff format --check
147 files already formatted

.venv/bin/pytest tests/test_extract_grammar.py tests/test_extract_profiles.py tests/test_extract_backend.py -q --disable-socket --tb=short
248 passed in 3.65s

.venv/bin/pytest -m 'not llm' -q --disable-socket --tb=short -rs
1657 passed, 2 deselected, 1 warning in 45.75s

git diff --check
(no output; exit 0)
```

The warning is the existing intentional blocked-socket test; the two excluded
tests require live models. An initial lint check found one import-order issue,
which was fixed. All test runs passed. Local help/version, compiled-parameter
inspection and four native grammar parses passed as described above. The new
CLI profile choices are visible in `auris eval run --help`.

## Step 8d — newlines without indentation; literal property names

Implemented on `slice-1`, 2026-11-13, before fresh newline-profile inference.
Re-read PLAN, including Appendix A, and the FHIR reference; no disagreement
with either. The owner reports both Step 8c compact profiles rejected:
Bonsai 27B stopped early with recall 0.60 versus 0.90 for pretty JSON, while
Gemma E4B had recall 0 and mostly empty assertion lists. These are supplied
harness-check results, not measurements performed in this step. `full-v0`
remains the default. No schema, prompt bytes, parser, mapper, evidence guard,
scorer, model pin, review rule or dependency changes.

### Pre-registered contracts

| Profile | Schema / order | Prompt bytes/version | Decoding |
|---|---|---|---|
| `grammar-newline` | `1` / canonical | Unchanged V3 / `extraction_v3` | `newline-gbnf-v1` |
| `grammar-newline-quotes` | `1` / canonical | Unchanged short-quote-only ablation / `extraction_abl_quotes_v1` | `newline-gbnf-v1` |

The quotes profile changes only V3's evidence bullet to V4's shortest exact
span instruction, as already frozen in Step 8b. Prompt SHA-256 values remain
`0db6b464e45c02bfe5f16ee9f392f4b8456ecb3ceabcef71611fbdabd31ab581`
and `fcb8438d00ae90cc7841beada9ae67314772d7ed572e44d32461250a8cb40521`.
Neither adds a formatting or optional-field instruction. These are the same
prompt bytes covered by the earlier full-corpus leakage audits; existing
fixture/demo/harness/annotation leakage tests now cover both new identities,
and evaluation retains its per-profile pre-inference leakage gate.

### Grammar and identity

`json_grammar(schema, whitespace="compact"|"newline")` shares the deterministic
converter; `compact_grammar(schema)` and `newline_grammar(schema)` expose each
mode. Newline mode uses `ws ::= "\n"?`: **zero or one LF at every JSON token
gap**, including before/after the root, around colons, before/after commas,
and immediately inside object/array delimiters. Spaces, tabs, CR/CRLF, repeated
LF and indentation are rejected outside strings. Newlines are optional;
compact output is still allowed. For example, `},\n{` is allowed between
assertions, and `\n    "kind"` is rejected. Empty containers and omitted
optional properties each retain a single whitespace slot rather than two
adjacent optional slots. Existing string-value whitespace and JSON escaping
remain intact, including escaped controls, Unicode and paired surrogates.

Both modes now emit property names and enum values as exact literal strings.
For example, `{"\u0061ssertions":[]}`, fully Unicode-escaped spellings of that
key, `"k\u0069nd"` and `"sympto\u006d"` are rejected by the grammar. The
unchanged JSON parser can still decode equivalent escaped spellings in saved
responses; the grammar deliberately constrains new generation. Generic fixed
names/enums that cannot be represented without JSON escapes fail conversion
instead of admitting an escaped spelling. All extraction-schema keys/enums
are directly representable.

Canonical schema-v1 grammar identities (UTF-8 bytes):

| Mode | Grammar version | Bytes | SHA-256 |
|---|---|---:|---|
| Compact, corrected literals | `compact-gbnf-v2` | 4,370 | `9bc29ac88cef31951b407362dd0ed3860894dcc8b81d3d8eed970545a894e1e8` |
| Newline | `newline-gbnf-v1` | 4,373 | `25436cfa1b5e97577694bbdc1a483c98bdee362a81cfed2247f74e68a4ae82db` |

The new profiles record `grammar_version`, `grammar_sha256` and the existing
profile/schema/prompt/property-order identity in extraction and evaluation
metadata. `decoding.json_whitespace` is `grammar_newline_json`. Measurement
and generation both receive the same top-level `grammar` without
`response_format`, through the existing transport. No server flags change.

The six schema-based profiles' request bytes and metadata remain unchanged;
their committed request-byte hash regressions still pass. The requested
literal fix also applies to the two rejected compact profiles, so **their
grammar request bytes necessarily change** and their grammar version advances
from `compact-gbnf-v1` to `compact-gbnf-v2`. Old v1 runs retain their recorded
hash/version and replay from frozen responses; they are not relabelled or
regenerated. Historical Step 8c descriptions and hash above describe v1.

### Offline coverage and limits

The independent emitted-GBNF interpreter now exercises both whitespace modes,
both schema versions and both property orders: 3,168 seeded valid documents
plus invalid mutations, optional-member combinations, string escapes and array
bounds. Explicit tests reject escaped spellings of every extraction property
and every enum value, accept value escapes, and reject spaces/tabs/CR/CRLF,
indentation and repeated LF at every JSON gap. Separate empty-container tests
cover zero-length arrays and all-optional objects. Transport/pipeline tests
cover measured input, generated output, usage agreement, observed whitespace,
grammar version/hash and prompt identity. The existing all-profile FakeBackend
evaluation, freeze/replay and leakage tests include the new profiles, and CLI
tests cover the exact grid below.

Grammar/parser equivalence is restricted to schema property order and literal
key/enum spellings, modulo the selected whitespace rule. The earlier two
post-schema qualifications still hold: the grammar preserves the schema's
64-assertion bound, while the unchanged parser refuses saturation; nonempty
all-whitespace evidence remains schema-valid but fails the nonblank validator.
All those refusals remain enforced in the complete extraction path.

No live HTTP/native-token decoding or model evaluation was performed here;
quality, early stopping and runtime are unmeasured for the new profiles.
Step 8c's Linux/XGrammar verification remains deferred. No default or adoption
decision changes.

### Claude's run

Run this four-model × two-profile grid sequentially into a fresh directory
(eight entries). `full-v0` control numbers already exist from Step 8b:

```sh
.venv/bin/auris eval run \
  --dataset eval/harness-check/manifest.json \
  --model bonsai-2-27b-q2_0 \
  --model gemma-4-e4b-it-q4_0 \
  --model qwen3-8b-q4_k_m \
  --model bonsai-2-9b-q2_0 \
  --extraction-profile grammar-newline \
  --extraction-profile grammar-newline-quotes \
  --out cache/eval-runs/step-8d-newline
.venv/bin/auris eval early-stop --run cache/eval-runs/step-8d-newline
.venv/bin/auris eval score --run cache/eval-runs/step-8d-newline
.venv/bin/auris eval compare --run cache/eval-runs/step-8d-newline
```

Inspect per-model recall/F1, pre-review critical errors, evidence failures,
assertion counts/early-stop share, tokens per assertion, completion time and
actual outside-string whitespace against the existing `full-v0` controls.
The generic early-stop/per-profile results remain available; the aggregate
full/lean-only comparison is unchanged. All harness examples are fictional;
these results are not clinical accuracy evidence.

Dependencies added: **none**. No manifest/lockfile edits, install, mise/uv
invocation, network operation, real-model inference, history change, commit or
push. Follow-ups: Claude's live grid, review and commit; existing deferred
Linux verification. No deviations from the approved clinical contract.

Final verification:

```text
.venv/bin/ruff check
All checks passed!

.venv/bin/ruff format --check
147 files already formatted

.venv/bin/pytest tests/test_extract_grammar.py tests/test_extract_profiles.py tests/test_extract_backend.py tests/eval/test_runs.py -q --disable-socket --tb=short
344 passed in 24.78s

.venv/bin/pytest -m 'not llm' -q --disable-socket --tb=short -rs
1736 passed, 2 deselected, 1 warning in 50.72s

git diff --check
(no output; exit 0)
```

The warning is the existing intentional blocked-socket test; the excluded
tests require live models. The first targeted run found four failures in the
new test serializer, which did not tokenize the numeric invalid-mutation
fixtures; adding JSON numeric tokens fixed the test helper. Ruff formatting
fixed two style findings. The passing summaries above include those fixes;
the final formatting-only edit does not change test behavior.

A read-only `load_run` check of `cache/eval-runs/step-8c-grammar-quick` also
loaded both 20-visit model entries with their original `compact-gbnf-v1`
version and SHA-256. Hashes of every file before/after the check were identical.
No saved run was rescored or modified. A standard-library Python inspection
also confirmed the two generated grammar byte counts/hashes recorded above.
