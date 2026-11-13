# Coordinator: pilot, agreement, adjudication and gold

Use two clinically trained independent annotators and a third clinically trained
adjudicator. Use pseudonyms A, B and C; identity attribution is not authentication
or verification of credentials. Keep work directories private and outside version
control. All commands below use already prepared local inputs. No command here
downloads anything, generates human answers or runs a model.

## Prepare and conduct the pilot

Read [the guidelines](guidelines.md), especially the independent agreement and
freeze section. Prepare an eight-encounter development pilot, then serve each
assignment in its own workdir:

```sh
.venv/bin/auris annotate pilot \
  --dataset eval/datasets/primock57-dev.json --n 8 --seed 1729 \
  --out out/annotation/pilot

.venv/bin/auris annotate serve \
  --assignment out/annotation/pilot/assignment-A.json \
  --workdir out/annotation/work-A

.venv/bin/auris annotate serve \
  --assignment out/annotation/pilot/assignment-B.json \
  --workdir out/annotation/work-B
```

For ACI-Bench use its development manifest and a separate pilot directory. A/B
must not see each other's answers or any model output. Each encounter must be
marked done, including explicit role review of every turn. Preserve the original
files unchanged. The selected fact editor opens with focus; on narrow windows it
stays in a scrollable bottom sheet, and on wide windows the side panel stays
reachable. Apply/Cancel returns focus and the selected words to the transcript.
Valid independent edits retain the 7a autosave behavior; Cancel closes the form
without undoing an earlier successful autosave.

## Measure agreement before adjudication

```sh
.venv/bin/auris annotate agreement \
  --a out/annotation/work-A/A --b out/annotation/work-B/B \
  --assignment out/annotation/pilot/assignment-A.json \
  --round 1 --overhead-hours 65 --out out/annotation/agreement-round-1
```

This prints the report and writes private `agreement.json` and `agreement.md`.
Directories may contain a single annotator's nested files. Mixed identities,
duplicates, incomplete encounters, different encounter sets, different guideline
hashes or different transcript hashes refuse. The optional assignment verifies
membership/hashes and supplies the frozen short/medium/long strata. Without it,
stratum is explicitly unknown; never infer length from timing or filenames.

Detection uses maximum-cardinality Hungarian matching on resource class,
experiencer, polarity, concept equivalence, and intersecting evidence in the same
turn. Concepts agree if canonical human concept IDs agree or the annotators'
acceptable surface texts overlap after the existing harness normalization.
No production lexicon enters the comparison. Detection F1 is
`2 * matches / (A facts + B facts)`; both-empty is undefined. Counts are symmetric;
there is no designation of either annotator as truth and no enumerable negative
set for a detection kappa. Each kind gets its own exact-kind alignment, so
cross-kind matches contribute to overall detection and kind disagreement but do
not inflate a kind's detection score.

Matched facts get kappa, raw agreement and separate A/B category prevalence for
kind, polarity, experiencer, temporality, medication intent, source role and each
critical attribute. Values, units, canonical values/units, dose, route, frequency,
concept IDs, code systems and code pairs use exact agreement. Inapplicable fields
are excluded and counted; missing applicable fields remain a category and are
counted. Two absent optional pieces can agree; inspect their missing counts.
Single-category kappa is undefined, including unanimous labels. All matched-label
metrics are conditional on matching: polarity/person already agree by the
matching rule; their disagreements instead reduce detection and become unmatched
adjudication tasks. Do not interpret conditional kappas as unconditional evidence
of person/polarity reliability.

The guideline's **required overall kappas** are **kind, polarity, experiencer and
temporality**. Freeze eligibility requires each to be defined and at least 0.70,
overall detection F1 at least 0.80, and every observed kind's detection F1 at least
0.80. The last check enforces the guide's prohibition on hiding difficult kinds
in an aggregate. Other kappas and per-kind label metrics are published for review;
they are not silently promoted to new freeze requirements. Absent kinds remain
undefined; the coordinator must judge whether the pilot covers the intended
schema, inspect rare categories and all per-kind results, and record the actual
freeze decision. An automated PASS is eligibility, not a claim that clinical
review happened. A degenerate sample cannot pass by treating undefined kappa as
one. Revise the guide between rounds and issue new hash-bound assignments. After
an unsuccessful third round: **narrow the schema; never lower the bar**.

Timing includes each encounter's active minutes for each annotator, grouped by
frozen length stratum, means and paired-encounter bootstrap uncertainty. The
repriced public budget is:

```text
H = overhead_hours + N_dev * measured_pair_mean_h + N_test * measured_pair_mean_h
measured_pair_mean_h = mean across encounters of (A active h + B active h)
```

The default **assumed fixed overhead** is 65 hours for training, coordination and
adjudication; replace it with measured/justified totals using `--overhead-hours`.
The old 1.5 h/dev and 2.4 h/test coefficients are replaced by the measured sum of
the two annotators' means. Both 40-dev/100-test and 40-dev/60-test totals are
printed. Intervals are deterministic 95% encounter percentile bootstraps (seed
1729, 2,000 resamples), preserving the A/B pairing; they are conditional on the
overhead assumption. A development pilot extrapolates to test; corpus/stratum mix,
idle-cutoff undercount and overhead uncertainty are not covered. One encounter
cannot estimate sampling uncertainty even if its bootstrap interval collapses.
Adjudication work records its own active time; use it to update the overhead when
available. Private-set annotation and the independent 30-encounter FHIR subset
remain separately budgeted. People and hourly rates remain owner decisions.

## Adjudicate with the third clinician

```sh
.venv/bin/auris annotate adjudicate \
  --a out/annotation/work-A/A --b out/annotation/work-B/B \
  --assignment out/annotation/pilot/assignment-A.json \
  --adjudicator-id C --clinically-trained \
  --out out/annotation/adjudication --prepare-only

.venv/bin/auris annotate serve \
  --assignment out/annotation/adjudication/adjudication-assignment.json \
  --workdir out/annotation/adjudication/work
```

Omit `--prepare-only` to build and immediately serve; the default workdir is
`<out>/work`. Resume using `serve` and the exact same assignment/workdir. The
explicit clinical-training flag is a coordinator attestation, not credential
verification. C must differ from A and B. Preparation needs a new/empty output
directory, so it cannot replace an existing assignment or adjudication history.

The issued assignment preserves both independent documents and an explicit list
of tasks. The local page shows their full answers and evidence quotes side by
side, with every differing field named (including critical subfields), plus
unmatched facts, important out-of-scope facts and role conflicts. Important facts
carry over on exact content equality; remaining overlapping spans form explicit
comparison tasks and all remaining facts are unmatched tasks. Notes/evidence
differences also require a decision. No model predictions, generated FHIR or
reference note is available in either annotation mode.

C accepts A, accepts B, or opens the existing human form and writes a corrected
fact. Corrected evidence uses the same verified selection endpoint and local code
lookup. Accepting the absent side of an unmatched fact, or choosing Omit, dismisses
that candidate. A corrected role must be explicit. **Every decision requires a
nonblank reason.** Agreed facts and roles carry forward automatically. The tool
keeps stable output IDs separate from the two annotators' local IDs. Completion
requires all tasks resolved and every turn role covered, and records a timestamp.

Private `work/adjudications/<encounter>.adjudication.json` files contain ordered
audit events: who, UTC time, choice, original A/B before-state, after-state and
reason. Revising a decision appends an event and reopens completion; previous
events remain. The local tool owns this history and atomically saves the full
append-only event sequence. It is not a tamper-proof identity service or an
externally immutable log. Independent originals are retained in the assignment.
The same loopback/token/origin protections and no-network policy apply.

## Export and protect test scoring

```sh
.venv/bin/auris annotate export-gold \
  --assignment out/annotation/adjudication/adjudication-assignment.json \
  --adjudicated out/annotation/adjudication/work/adjudications \
  --intakes out/annotation/pilot/fictional-intakes \
  --out out/annotation/gold
```

Point `--adjudicated` at the records folder, **`work/adjudications`**, not the
parent assignment or work directory. In the workflow above this is
`out/annotation/adjudication/work/adjudications`. If no `*.adjudication.json`
files are found, export names the supplied folder and explains this path.
An encounter-set mismatch lists the missing and unexpected encounter IDs.

The complete assigned encounter set must be adjudicated. Export checks the trusted
frozen manifest, assignment/audit identity, evidence and the unchanged 7a fictional
anchors before writing. It packages the harness `manifest.json`, stand-off gold,
original and role-corrected transcripts, exact intake files, independent answers
and audit records. The scoring transcript's hash reflects adjudicated turn roles;
`source_transcript_sha256` retains the frozen original hash. Span text/offsets do
not change. Provenance includes A/B/C IDs, independent/adjudicated status and the
guideline hash. The existing loader verifies the completed package. Export does
not run inference or score it. The offline FakeBackend regression tests package
compatibility; they are not human agreement or clinical accuracy results.

The scoring manifest represents the known RxNorm, ICD-10-CM, LOINC and SNOMED
system URIs with the harness's existing short tags (`rxnorm`, `icd10cm`, `loinc`,
`snomed`), including code-based concept IDs. This changes their serialization,
not their code or concept identity; it adds no terminology content or licensed
SNOMED. Full human URI IDs/codes remain unchanged in the assignment and audit.
Unknown namespaces and uncoded `auris:` concept IDs remain literal. This adapter
is necessary for exact/code-only comparisons with existing frozen predictions;
the scorer and its versions are unchanged.

**Human test gold can be created before the locked model evaluation.** Test
serving and gold export still require `--locked-test-run` on first use of a cache
ledger, or `--confirm-locked-test-rerun` when that split/content has already been
reserved (including earlier A/B/C annotation sessions). Export uses the same
`cache/eval/test-ledger.jsonl` as 7a and evaluation, conservatively reserving an
operation before opening transcripts or creating gold output. A flag does not
relabel a frozen test encounter as development. Preserve the shared ledger.

Gold creation does **not** authorize model inference or loading it **for scoring**.
Use the existing evaluation CLI, which independently enforces its one-run
reservation and score lock against that same ledger. `gold.load_dataset` is a
low-level integrity loader, not an authorization boundary; direct low-level
scorer calls must never be used to bypass the CLI's lock. Human annotation
reservations mean that the authorized later evaluation needs explicit rerun
confirmation under this intentionally conservative shared-ledger policy. Keep
test gold away from tuning/prompt development. Publish only reviewed aggregate
agreement and evaluation reports, never private annotation files by accident.
