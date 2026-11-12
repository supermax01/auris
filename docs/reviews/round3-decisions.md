# Round-3 decisions (merge of Claude, Muse, Codex, Gemini)

Merged 2026-11-10 by Claude Opus 5.5 (Claude Code) at the owner's request.
All four round-3 reviewers returned **AMEND**; none blocked. Every FHIR claim
below was checked against hl7.org R4 / US Core STU6.1 pages
([../fhir-reference.md](../fhir-reference.md)); dataset and artifact claims
via primary sources (ACI-Bench and PriMock57 READMEs, Hugging Face, LOINC).

## Facts that settled disputes

- **ACI-Bench ships no public audio** (README describes conversations, ASR
  transcripts and notes; no audio folder; abstract says encounters are not
  routinely recorded). Gemini's "40 audio-verified ACI-Bench test" split is
  rejected as unsupported. ACI-Bench is text-only for Auris.
- **PriMock57:** 57 consultations, per-speaker WAV channels (16 kHz),
  CC BY 4.0. ACI-Bench: 67 train / 20 valid / 3×40 test, CC BY 4.0;
  README documents swapped ASR speaker tags.
- **MedicationRequest.requester is 0..1** in US Core 6.1.0; `us-core-21`
  requires it only for order-family intents. **authoredOn is 0..1** (Must
  Support) — Muse's "authoredOn 1..1" (from memory) is rejected.
- **ait-1 is an error-level rule**; con-3 is best-practice only; US Core
  adds no Condition clinicalStatus requirement.
- **US Core NKA = SNOMED 716186003 with verificationStatus=confirmed**, not
  `refuted`.
- **No official faster-whisper large-v3-turbo conversion** exists (only
  community repos); `openai/whisper-large-v3-turbo` is MIT and multilingual.
  **`Qwen/Qwen3-8B-GGUF` is official** (Q4_K_M available; 32,768 native ctx).
- **LOINC licence** permits redistributing a subset (records may be deleted)
  under its notice terms.

## Amendment outcomes

| # | Outcome | Adopted wording source |
|---|---|---|
| A1 allergy clinicalStatus | Adopted, edited | Codex: historical reaction ≠ resolved risk; affirmed allergy → active, explicit outgrown/resolved → review |
| A2 condition clinicalStatus | Adopted, edited | Muse + Codex: temporality (not speaker) sets status for both rows; historical/unclear → review |
| A3 lane states | Adopted, edited | Gemini: lanes stay PASS/FAIL/NOT_CHECKED; uncoded concept = mapper flag `uncoded_text_only` + review reason |
| A4 splits/budget | Adopted, edited | PriMock57 17/40 (all three); ACI-Bench text-only 23/60 (Codex); budget formula (Codex); private set size → owner |
| A5 extractor selection | Adopted, edited | Codex + Muse: 40 dev; point-estimate rule, CI reported not gating; wide CI ⇒ "provisional" label |
| A6 frozen config | Adopted, edited | Self-converted large-v3-turbo INT8; official Qwen3-8B Q4_K_M; n_ctx 8192 (Codex, Gemini); M2 16 GB ≤12 GiB (Muse, Codex) |
| A7 operations | Adopted, edited | Muse: CLI is the oracle, warm wrapper only after contract test; Codex/Muse chunk/merge rules; "stable assertion IDs" dropped |
| A8 exits | Adopted, edited | Thresholds: Muse/Gemini role ≥0.90 @ ≥0.85 coverage; Muse/Codex ≤5-pt F1 loss; Slice 3 exact frozen-prediction reproduction |
| A9 tier-0 export / review | Adopted, edited | Muse + Gemini: "validated" reserved for terminology PASS; local review log, not Provenance; review cannot waive failures (Codex) |
| A10 intake + SC3 | Adopted, edited | Intake populated to US Core minimums (verified); SC3 public-data wording |
| A11 process record | Adopted | Deferred list with revisit triggers (Muse) |

## Missed defects adopted

- Vital time inheritance limited to in-visit measurements (Muse M2, Codex).
- Lanes derived from checker scope + issue code/expression, not severity (Codex).
- Requester = identified initiating clinician, ambiguity → review (Codex).
- Mandatory category slices and fixed LOINC/UCUM codes (Gemini M2, verified).
- Strict match requires value+unit / dose-route-frequency equivalence (Gemini M1).
- Idempotent transaction proof with client-assigned ids + PUT; server
  acceptance reported separately from validator lanes (Codex, Muse, Gemini M4).
- Clinician may approve text-only coding of allergens absent from
  Prescribable RxNorm, recorded in the review log (Gemini M3).
- ACI-Bench speaker/role gold adjudicated, not taken from tags (Codex).
- Concept equivalence from blind adjudicated gold concept IDs; rule-of-three
  bound on zero critical errors; transcript-correction budget line (Muse M3).
- PHI handling: 0600/0700 permissions, no transcript text in info logs (Muse M4).
- LOINC short licence + UCUM terms inventory in NOTICE (Codex, Muse M5).

## Rejected

- Gemini A4 ACI-Bench audio counts (no public audio).
- Gemini A6 "float16 on Apple Silicon" (CTranslate2 has no Metal backend;
  Mac runs CPU INT8) and the 90 s runtime target (unmeasured).
- Muse M1 "authoredOn 1..1" (verified 0..1).
- Exporting a FHIR Provenance resource in v1 (needs Organization via
  provenance-1; scope creep).
- Codex's per-class recall ≥0.80 as a release gate (F1 ≥0.80 stays a target;
  release gates on honesty and critical errors, not a score).
