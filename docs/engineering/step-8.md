# Step 8 — lean extraction profile

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
