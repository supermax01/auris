# Step 6d — overlap avoidance, scorer v3 and field hygiene

This step changes one fictional prompt sentence, refines safety classifications,
and rejects unusable extracted fields before mapping. No dependency, clinical
code, model pin, split membership, gold annotation or FHIR rule changed. There
is no disagreement with `docs/fhir-reference.md`. All work used the existing
project venv and local artifacts; no download, inference, commit or push ran.

## Leakage review and prompt v3

```sh
.venv/bin/auris eval leakage-report --dataset primock57 --prompt-version extraction_v2
.venv/bin/auris eval leakage-report --dataset primock57
.venv/bin/auris eval leakage-report --dataset aci-bench
.venv/bin/auris eval leakage-report --dataset eval/harness-check/manifest.json
```

`--dataset` accepts either public corpus ID, a public split manifest (auditing
the full eligible corpus), or an evaluation gold manifest. Public corpus reads
verify pinned commits and source checksums. No test extraction is reserved.
The optional `--prompt-version` supports v1/v2/v3; the default is active v3.
The command exits successfully after writing diagnostic results even when the
guard reports failure; the JSON retains that failure. Setup and inference still
refuse failed guards.

Detailed sentence hits go only to repository
`cache/eval/leakage-reports/<dataset-id>-<prompt-version>.json`, at 0600 within
0700 directories. Output location cannot be redirected with `--out` or
`AURIS_CACHE`; existing symlink parents are refused. Each exact/near-duplicate
hit includes the original sentence fragments, encounter ID and Jaccard score.
Only deduplicated prompt sentences reach stdout; a passing report is silent.
Corpus sentences never enter committed files or stdout. Matching normalization,
thresholds, short-sentence behavior and clinical-watchword policy are unchanged.

The sole v2 near-duplicate was the fictional example **“I live with migraine.”**
V3 substitutes **“Migraine affects me personally.”** The labels, other examples,
and all rules are byte-identical to v2. Both v1 and v2 files are unchanged.

V3 SHA-256:
`0db6b464e45c02bfe5f16ee9f392f4b8456ecb3ceabcef71611fbdabd31ab581`.
Length: 4,205 characters; **909 tokens** measured offline with the pinned Qwen
GGUF tokenizer (v2: 907); conservative estimator: 1,402 tokens. This is prompt
text only, with default special-token handling, not rendered chat/schema input.

```sh
llama-tokenize --offline -m cache/models/Qwen3-8B-Q4_K_M.gguf \
  -f src/auris/extract/prompts/extraction_v3.md --show-count --ids
```

| Corpus | Encounters | V3 exact hits | V3 near duplicates | Guard |
|---|---:|---:|---:|---|
| PriMock57 | 57 | 0 | 0 | PASS |
| ACI eligible canonical pool | 195 | 0 | 0 | PASS |
| Fictional harness-check | 20 | 0 | 0 | PASS |

The recursively discovered fixture transcripts also pass their stricter clinical
watchword guard. `auris setup datasets` succeeds. The two committed quote-free
leakage audits now describe v3; source, adaptation, coverage and split manifests
remain unchanged. Step 6c's engineering note documents its historical v2 failure.

## Scorer v3 and retained v2 replay

`auris eval score --scorer-version 2|3` defaults to v3. Frozen inference runs
from versions 1, 2 and 3 remain loadable. Reports record both inference-time and
replay-time scorer versions/hashes. `compare` accepts either supported replay
version after checking the score receipt and current source hash.

- `wrong_person` requires a patient/non-patient crossing. Family-member versus
  other is an `attribute_errors` event. Unknown/missing attribution mismatches
  also remain non-critical attributes; they do not establish a record crossing.
- Drug/code mismatches remain critical. Dose comparison requires explicit
  number/unit amounts on both sides, using the existing scorer-owned spelling
  normalization for mg, mcg, g, mL and units. No conversions or number-word
  interpretation are added. Amount tuples, not surrounding schedule prose, are
  compared. An absent amount cannot establish a wrong dose.
- Non-null dose text without a recognized amount, including “once daily”, is
  a `field_errors` event. Literal null strings in value/unit/dose/route/frequency/
  stated-time fields and commentary in value/unit/dose fields are also field
  errors. These diagnostics inspect raw predictions independently of pipeline
  hygiene; they do not rewrite predictions or change strict matching.
- `order_never_given` is unchanged, including continuing/discussed medicines
  mistaken for new orders. The other critical categories are unchanged.

Both new non-critical categories have counts, affected-encounter counts,
per-encounter prediction IDs, and item-level gold/prediction/evidence diagnostics
in JSON and Markdown. Counts are pre-review, at most once per assertion per
category; one assertion can contain several bad fields, described together.
Lenient, assertion-strict and record-strict matching remain unchanged.

## Consistency-lane field hygiene

The original extraction remains intact for scoring and audit. Consistency
results identify cleaned/unusable field names; the mapper and refusal ledger
receive a copy with those fields set to null.

- Trimmed, case-insensitive literal `null`, `none`, `n/a` and `unknown` in the
  six eligible field types become null. `literal_null_normalised` is an
  informational flag, not a review reason. Concept/allergen fields are never
  cleaned this way.
- Parentheses or the whole words/phrase `implied`, `not explicitly stated`,
  `presumably`, `likely`, `context` in value/unit/dose fields make the entire
  field unusable and require review with `field_contains_commentary`.
- An unusable unit is recovered only from a recognized unit immediately after
  the matching numeric value(s) in aligned evidence, using existing unit tables
  and profile checks. Missing, ambiguous or unsupported recovery refuses the
  record. The commentary is never stripped to obtain a plausible unit.
- An ordinary absent unit retains existing BP/HR fixed-unit policy. A unit
  explicitly rejected as commentary must have quote support, even for BP/HR.
  Thus the exact Qwen BP commentary with a quote lacking a spoken unit still
  refuses, now with a precise review reason. With a spoken unit it maps and
  remains review-required. Step 5d's split BP value/unit recovery still works.
- Unusable values refuse measurements; unusable dose text is omitted from draft
  proposals, which still require review. The literal Qwen `dose_text: "null"`
  no longer causes a dosage-quote mapping refusal.

Empty new consistency metadata is omitted in serialization, preserving existing
FHIR golden artifacts. Nonempty field hygiene metadata is retained. No failed
validation lane can be waived through these changes.

## Frozen harness-check-2 replay

Copied all 126 hashed artifacts and unchanged `run.json` into
`cache/eval-runs/harness-check-2-v3-rescore/`, with private permissions. The
original run directory is unchanged. First replayed scorer v2 there: **every
model metric and diagnostic matched the original `scores.json` exactly**.
Then generated the final v3 score and comparison:

```sh
.venv/bin/auris eval score --run cache/eval-runs/harness-check-2-v3-rescore --scorer-version 2
.venv/bin/auris eval score --run cache/eval-runs/harness-check-2-v3-rescore --scorer-version 3
.venv/bin/auris eval compare --run cache/eval-runs/harness-check-2-v3-rescore
```

| Pre-review category | Qwen v2 → v3 | Gemma v2 → v3 |
|---|---:|---:|
| allergy_denial_flipped | 0 → 0 | 0 → 0 |
| wrong_drug_or_dose | 2 → 0 | 0 → 0 |
| wrong_person | 1 → 0 | 1 → 0 |
| order_never_given | 2 → 2 | 0 → 0 |
| denied_condition_present | 0 → 0 | 0 → 0 |
| Total critical category events | **5 → 2** | **1 → 0** |

Every exported-without-review critical count remains zero for both models.
Qwen's retained critical events are hc-08 cetirizine and hc-13 metformin orders.
Both models have one non-critical attribution event (hc-06 gout). Qwen has
five field-error assertions in four encounters: BP commentary in hc-03/hc-05,
literal-null medication fields and a separate literal-null height time in hc-15,
and frequency-only dose in hc-19. Gemma has zero field-error assertions.

All lenient and strict results, frozen predictions/resources and inference
metadata are unchanged. Reports carry the fictional harness-check warning;
these are not clinical accuracy evidence. Pipeline hygiene was not rerun on
frozen predictions. Claude's fresh inference will measure its effects.

## Verification and remaining work

Final checks used the direct project venv with networking disabled:

```text
.venv/bin/ruff check
All checks passed!

.venv/bin/ruff format --check
121 files already formatted

.venv/bin/pytest -m 'not llm' -q --disable-socket --tb=short -rs
Validator wall-clock time — goldens + anchors: 6.248s
Validator wall-clock time — demo + display variants: 6.145s
Validator wall-clock time — broken resources: 8.167s
1359 passed, 2 deselected, 1 warning in 27.33s

.venv/bin/auris setup datasets
primock57: commit and source checksums OK; prepared 57 transcripts
aci-bench: commit and source checksums OK; prepared 195 transcripts
frozen splits verified; gold: none (awaiting clinician annotation)
public clinical-term overlaps are warnings; see prepared/*-leakage.json for counts

.venv/bin/python -m auris.datasets.audit
primock57: all=57, dev=17, test=40
aci-bench: all=195, dev=23, test=60
coverage and splits frozen/verified; no extraction or scoring performed

.venv/bin/auris eval score --run cache/eval-runs/harness-check-2-v3-rescore --scorer-version 3
scored frozen predictions; wrote scores.json; fictional harness-check set, NOT accuracy evidence

.venv/bin/auris eval compare --run cache/eval-runs/harness-check-2-v3-rescore
not applicable: fictional harness-check set
wrote report.md and report.json

git diff --check
(no output; exit 0)
```

The warning is the existing deliberate socket-blocking test; the two real-model
tests were deselected. Earlier targeted/full runs caught stale version snapshots,
empty consistency serialization, and an invalid authored test input; all were
fixed before the final full run. Additional checks used read-only git status/
branch/diff, `rg`, `cat`, `sed`, `head`, `command -v`, `ruff format`,
`ruff check --fix`, direct Python inspection/private artifact copying, prompt
tokenization, and public split/fixture leakage reports. Private report permissions,
frozen artifact byte identity and scorer hashes were verified explicitly.

Dependencies added: **none**. Auris remains Apache-2.0.

## Files created or changed

- `src/auris/eval/leakage_report.py`: private sentence diagnostics and cache confinement.
- `src/auris/eval/cli.py`: leakage-report command and explicit scorer-version selection.
- `src/auris/extract/leakage.py`: expose original sentence fragments without changing matching.
- `src/auris/extract/prompts/extraction_v3.md`: reword the single overlapping example.
- `src/auris/extract/prompt.py`: select v3 as the active prompt.
- `eval/datasets/primock57-leakage.json`: refresh quote-free audit for passing v3.
- `eval/datasets/aci-bench-leakage.json`: refresh quote-free audit with v3's hash.
- `src/auris/eval/__init__.py`: default scorer v3 and supported replay versions.
- `src/auris/eval/normalization.py`: recognize explicit normalized dose amounts.
- `src/auris/eval/critical.py`: refine v3 critical events while retaining v2 behavior.
- `src/auris/eval/noncritical.py`: independent attribute and field-error findings.
- `src/auris/eval/metrics.py`: non-critical counts/events and version propagation.
- `src/auris/eval/diagnostics.py`: item-level non-critical analysis and version propagation.
- `src/auris/eval/reports.py`: render new counts and accept supported scorer replays.
- `src/auris/eval/runs.py`: load old runs and score with the selected version.
- `src/auris/consistency.py`: null/commentary hygiene with auditable field metadata.
- `src/auris/fhir/mapper.py`: map sanitized fields and retain informational flags.
- `src/auris/fhir/vitals.py`: recover rejected unit fields only from aligned evidence.
- `tests/eval/test_leakage_report.py`: privacy, stdout, symlink and prompt-change regressions.
- `tests/eval/test_scorer_v3.py`: attribution, dose, order and non-critical regressions.
- `tests/eval/test_normalization.py`: retain the old normalization expectations under v2.
- `tests/eval/test_runs.py`: exercise both scorer versions and update active-version expectations.
- `tests/test_field_hygiene.py`: field/null rules, exact Qwen outputs and BP recovery tests.
- `tests/test_extract_leakage.py`: update expected active prompt version.
- `tests/test_extract_pipeline.py`: pin the v3 prompt hash.
- `docs/engineering/step-6d.md`: implementation, commands, verification and replay results.

Generated private artifacts are the leakage reports described above, refreshed
setup audits under `cache/datasets/prepared/`, and the copied frozen run plus
`scores.json`, `score-receipt.json`, `report.json`, `report.md` under
`cache/eval-runs/harness-check-2-v3-rescore/`. They remain gitignored.

Deviations/open questions: none. Follow-ups: Claude's fresh inference; existing
clinician annotation/adjudication and independent evaluation work remain outside
this step. In particular, this step does not reinterpret `stated_time_text:
"current"`, invent units, relocate frequency text out of the dose field, or add
new dose-unit equivalences.
