# Auris visit summary

**FICTIONAL DEMO — no real patient data; scripted times, no recording.**

Research software, not cleared or approved for clinical use; clinician review mandatory. Regulatory classification is deployment-specific.

Model: qwen3-8b-q4\_k\_m · Prompt: extraction\_v2 · Run: 2026-11-12T12:00:00+00:00

Evidence times use aligned audio offsets or segment starts; segment times are approximate.

Review entries refer to the item labels below (S, O, A, P and D).

Bundle: intake anchors only; no extracted records exported. Unresolved material assertions block final export (PLAN A4). Coding not checked (tier 0).

## S — Subjective (quoted from the transcript; symptoms are not FHIR records in v1)

- S1 — Symptom: “I have had a cough for four days” [00:13 · patient] — transcript-supported · ledger only · coding not checked
- S2 — Symptom: “No fever” [00:40 · patient] — transcript-supported · ledger only · needs clinician review · not validated · coding not checked
- S3 — Allergy: sulfa (unconfirmed); stated reaction: rash [01:57 · patient] — transcript-supported · ⚠ needs clinician review · not validated · drug family, kept as text · needs coding review · profile-checked · coding not checked
- S4 — Allergy denial: “I am not allergic to penicillin” [02:18 · patient] — transcript-supported · ledger only · needs clinician review · not validated · coding not checked

## O — Objective

- O1 — Blood pressure 126/78 mmHg [02:36 · clinician] — transcript-supported · eligible for export · profile-checked · coding not checked
- O2 — Heart rate 84 /min [02:44 · clinician] — transcript-supported · eligible for export · profile-checked · coding not checked

## A — Assessment

- A1 — Patient-reported: asthma (unconfirmed, active) [01:01 · patient] — transcript-supported · ⚠ needs clinician review · not validated · kept as text · needs coding review · profile-checked · coding not checked
- A2 — Family history (not the patient's condition): “My father has diabetes” [01:11 · patient] — transcript-supported · ⚠ needs clinician review · not validated · coding not checked
- A3 — Assessed: acute bronchitis (provisional, active) [02:58 · clinician] — transcript-supported · ⚠ needs clinician review · not validated · kept as text · needs coding review · profile-checked · coding not checked
- A4 — Ruled out: pneumonia (refuted) [03:11 · clinician] — transcript-supported · ⚠ needs clinician review · not validated · kept as text · needs coding review · profile-checked · coding not checked

## P — Plan

- P1 — DRAFT — acetaminophen — 500 milligrams by mouth every eight hours as needed [03:39 · clinician] — transcript-supported · ⚠ needs clinician review · not validated · profile-checked · coding not checked

## Needs review — not validated

- S2 [00:40 · patient] — transcript-supported · ledger only · needs clinician review · not validated · coding not checked — A denial requires review before any record export
- A1 [01:01 · patient] — transcript-supported · ⚠ needs clinician review · not validated · kept as text · needs coding review · profile-checked · coding not checked — A clinician must review the coding; No unambiguous local code; text retained
- A2 [01:11 · patient] — transcript-supported · ⚠ needs clinician review · not validated · coding not checked — This statement concerns someone other than the patient; Cannot create a patient record for another person
- D1 [01:35 · patient] — transcript-supported · ledger only · needs clinician review · not validated · coding not checked — Medicines require clinician review
- S3 [01:57 · patient] — transcript-supported · ⚠ needs clinician review · not validated · drug family, kept as text · needs coding review · profile-checked · coding not checked — Allergies require clinician review; A clinician must review the coding; No unambiguous local code; text retained
- S4 [02:18 · patient] — transcript-supported · ledger only · needs clinician review · not validated · coding not checked — Allergies require clinician review; A denial requires review before any record export; The installed code set cannot represent this allergy denial as a record; Clinician confirmation is required
- A3 [02:58 · clinician] — transcript-supported · ⚠ needs clinician review · not validated · kept as text · needs coding review · profile-checked · coding not checked — A clinician must review the coding; No unambiguous local code; text retained
- A4 [03:11 · clinician] — transcript-supported · ⚠ needs clinician review · not validated · kept as text · needs coding review · profile-checked · coding not checked — A denial requires review before any record export; The ruled-out problem requires clinician confirmation; A clinician must review the coding; No unambiguous local code; text retained
- D2 [03:28 · clinician] — transcript-supported · ledger only · needs clinician review · not validated · coding not checked — Medicines require clinician review; A denial requires review before any record export
- P1 [03:39 · clinician] — transcript-supported · ⚠ needs clinician review · not validated · profile-checked · coding not checked — Medicines require clinician review

## Discussed, not captured as records

- D1 — Continuing medicine (not a new order): “I still use albuterol” [01:35 · patient] — transcript-supported · ledger only · needs clinician review · not validated · coding not checked
- D2 — Discussed medicine (not ordered): “We could try amoxicillin, but I don't think you need an antibiotic” [03:28 · clinician] — transcript-supported · ledger only · needs clinician review · not validated · coding not checked
