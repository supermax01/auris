# Round-3 review: locked PLAN.md (Claude Opus 5.5, Claude Code)

**Verdict: AMEND before "go".** The plan absorbed nearly every round-2 blocker
well (export matrix, BP panel, vital time inheritance, negation routing,
requester anchor, transaction Bundle, evidence alignment, Docker
no-vendoring, budget envelopes, leakage discipline). One defect would fail
validation on every exported allergy; the rest are bookkeeping gaps that
must be closed so Slice 1 starts from a complete contract.

Method: read every file in `docs/` (PLAN, team brief, competitive analysis,
research prompts, both round-1 drafts, all three round-2 reviews) and
cross-checked each reviewer's requested change against PLAN.md. FHIR R4
invariants ait-1/ait-2 and con-3/con-4/con-5 were checked against
hl7.org/fhir/R4 pages on 2026-11-10 (descriptions and severities verbatim
from the Structure tables; FHIRPath expressions not re-verified).

## Defects

**D1. AllergyIntolerance row omits `clinicalStatus` — every exported
allergy fails base R4 (confirmed).** R4 `ait-1` (Rule): "clinicalStatus
SHALL be present if verificationStatus is not entered-in-error." The row
sets only `verificationStatus=unconfirmed`. Fable's A3 and Gemini's
Amendment 1 both set `clinicalStatus=active`; it was lost in the merge.

**D2. Condition rows lost `clinicalStatus`, and there is no rule for
deriving it.** For problem-list-item Conditions R4 `con-3` is only a
Guideline (warning), so this is not a hard base-R4 failure (US Core 6.1.0
tightening not yet checked). The real issue is semantic: "history of
appendicitis" must not become `active`. A temporality → `clinicalStatus`
rule is needed. (Separately, Gemini's citation of con-3 to justify
omitting `clinicalStatus` on refuted encounter-diagnoses is wrong — con-3
concerns problem-list-item — though omission is permitted: 0..1.)

**D3. `NEEDS_CODING_REVIEW` is not a lane state.** Lanes are defined as
PASS / FAIL / NOT_CHECKED; the allergy row introduces a fourth value.

**D4. The test set does not add up.** The 375h budget assumes 117 test
encounters (57 PriMock57 + 60 ACI-Bench). The plan also (a) excludes
PriMock57 recordings used in Slice 2 development from held-out claims and
(b) limits ACI-Bench items without audio to text metrics. The held-out
audio test set is therefore undefined and the budget is not reconciled.
The newly commissioned private set has no size, cost, or budget line.

**D5. The extractor flip criterion is unreachable on 20 dev encounters.**
"≥3 F1 with CI excluding 0" on 20 encounters will almost never trigger, so
8B stays default by construction. Fable raised this in round 2; unanswered.

**D6. Reproducibility pins missing.** The laptop ASR model is unnamed
("INT8 CPU"); no quantisation artifact, `n_ctx`, chat template revision,
reference laptop, or memory budget. Codex asked for these to be frozen.

**D7. Operational decisions missing.** Warm validator mechanism (CLI
`-server` mode vs validator-wrapper vs HAPI `$validate`), JVM provisioning
on Mac, minimum laptop spec, sequential model load order, long-encounter
chunking and assertion de-duplication (raised in both round-1 drafts).

**D8. No slice has a measurable exit.** Slice 2 says "see Validation";
Validation has no slice threshold. Release criteria lack minimum coverage
and reference hardware (Codex Q5).

**D9. Tier-0 export and review authority unresolved.** Can a non-material
resource be finally exported when the terminology lane is NOT_CHECKED, and
what does that export claim? Who may resolve a material assertion, and how
is that recorded? (Codex Q5, unanswered.)

**D10. Success-criterion tensions.** (a) SC1's one-command demo needs a
bundled fictional intake (Patient, Encounter, Practitioner) that the plan
never mentions. (b) SC3 "a stranger can reproduce our numbers" can only
cover the public portion while the private set and annotation licence are
undecided.

**D11. Process record.** The header says all four teammates reviewed;
Gemini never wrote a round-1 draft (Fable A7 asked that this be recorded).
Several suggestions were dropped without a recorded reason: no-diariser
text-only baseline, `gpt-oss-20b` and large-v3-turbo comparators, Parakeet
second-opinion ASR on medication/allergy spans, loudspeaker-replay
acoustic stress set, HealthSage check.

## Proposed amendments (for critique — accept, edit, or reject)

- **A1 (D1).** Allergy row: `clinicalStatus=active` when stated as current;
  stated-as-past/outgrown → review, never exported as active.
- **A2 (D2).** Clinician-assessed problem: `clinicalStatus=active`.
  Patient-reported condition: extracted temporality maps current → `active`,
  explicitly past/resolved → `resolved`; unclear temporality → review, not
  exported. No `abatement[x]` is ever inferred (con-4).
- **A3 (D3).** Non-drug allergen without SNOMED: terminology lane
  `NOT_CHECKED` plus review reason `needs_coding_review` (a review reason,
  not a lane state).
- **A4 (D4).** Before Slice 2 starts, freeze a PriMock57 split into a
  dev pool (usable in Slice 2) and a test pool (untouched); enumerate
  ACI-Bench items as audio-bearing vs text-only at setup; recompute the
  annotation budget from those counts. Private commissioned set: owner
  decides size and whether it is inside or outside the budget envelope.
- **A5 (D5).** Enlarge dev to ≥40 encounters (ACI-Bench validation split +
  train items), or declare 8B the provisional default and report the
  flip-criterion point estimate with CI regardless.
- **A6 (D6).** Add a frozen-configuration table set in Slice 1: ASR laptop
  model (candidate: distil-large-v3 or large-v3-turbo, INT8), Qwen3-8B GGUF
  quant + upstream revision, `n_ctx`, chat template, reference laptop
  (e.g. 16 GB Apple Silicon) with peak-RSS budget, reference Linux GPU.
- **A7 (D7).** Slice 1 picks and records the warm validator mechanism; JVM
  (Temurin 21) via mise; chunking: whole encounter when it fits `n_ctx`,
  else overlapping windows with stable assertion IDs and deterministic merge.
- **A8 (D8).** Slice 1 exit: every §3 row exercised, refusal case refused,
  network-disabled CI green, all lanes reported. Slice 2 exit: role
  accuracy and assertion-F1 drop vs oracle within thresholds fixed from
  Slice 1 baselines before Slice 2 starts. Slice 3 exit: clean-machine
  rerun reproduces public numbers within CI. Release: declared minimum
  coverage; no unadjudicated critical error.
- **A9 (D9).** Non-material resources passing schema/evidence/R4/profile
  lanes may be final-exported with terminology NOT_CHECKED stated in
  `validation.json`; material resources always need review. Only a
  reviewer configured with the clinician role resolves material items;
  reviewer identity and time are recorded (Provenance).
- **A10 (D10).** Ship a fictional `intake.json` with the demo; reword SC3
  to "reproduce our published public-data numbers".
- **A11 (D11).** Record in the header that Gemini contributed round-2 only;
  add a "Considered and deferred" list with one-line reasons.
