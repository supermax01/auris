# Auris visit summary

**FICTIONAL DEMO — no real patient data; scripted times, no recording.**

Research software, not cleared or approved for clinical use; clinician review mandatory. Regulatory classification is deployment-specific.

Model: qwen3-8b-q4\_k\_m · Prompt: extraction\_v2 · Run: 2026-11-12T12:00:00+00:00

Evidence times use aligned audio offsets or segment starts; segment times are approximate.

Bundle: intake anchors only; no extracted records exported. Unresolved material assertions block final export (PLAN A4). Coding not checked (tier 0).

## S — Subjective (quoted from the transcript; symptoms are not FHIR records in v1)

- Symptom: “I have had a cough for four days” [00:13 · patient] — transcript-supported · ledger only · coding not checked
- Symptom: “I have not had any fever during these four days” [00:40 · patient] — transcript-supported · ledger only · needs clinician review · not validated · coding not checked
- Allergy: sulfa (unconfirmed); stated reaction: rash [01:57 · patient] — transcript-supported · ⚠ needs clinician review · not validated · drug family, kept as text · needs coding review · profile-checked · coding not checked
- Allergy denial: “I am not allergic to penicillin” [02:18 · patient] — transcript-supported · ledger only · needs clinician review · not validated · coding not checked

## O — Objective

- Blood pressure 126/78 mm\[Hg\] [02:36 · clinician] — transcript-supported · eligible for export · profile-checked · coding not checked
- Heart rate 84 /min [02:44 · clinician] — transcript-supported · eligible for export · profile-checked · coding not checked

## A — Assessment

- Patient-reported: asthma (unconfirmed, active) [01:01 · patient] — transcript-supported · ⚠ needs clinician review · not validated · kept as text · needs coding review · profile-checked · coding not checked
- Family history (not the patient's condition): “My father has diabetes” [01:11 · patient] — transcript-supported · ⚠ needs clinician review · not validated · coding not checked
- Assessed: acute bronchitis, unspecified (provisional, active) [02:58 · clinician] — transcript-supported · eligible for export · profile-checked · coding not checked
- Ruled out: pneumonia (refuted) [03:11 · clinician] — transcript-supported · ⚠ needs clinician review · not validated · kept as text · needs coding review · profile-checked · coding not checked

## P — Plan

- DRAFT — acetaminophen — 500 mg by mouth every eight hours as needed [03:39 · clinician] — transcript-supported · ⚠ needs clinician review · not validated · profile-checked · coding not checked

## Needs review — not validated

- Symptom: “I have had a cough for four days” [00:13 · patient] — transcript-supported · ledger only · coding not checked — Kept in the discussion ledger; no FHIR record emitted
- Symptom: “I have not had any fever during these four days” [00:40 · patient] — transcript-supported · ledger only · needs clinician review · not validated · coding not checked — A denial requires review before any record export; Kept in the discussion ledger; no FHIR record emitted
- Patient-reported: asthma (unconfirmed, active) [01:01 · patient] — transcript-supported · ⚠ needs clinician review · not validated · kept as text · needs coding review · profile-checked · coding not checked — A clinician must review the coding; No unambiguous local code; text retained
- Family history (not the patient's condition): “My father has diabetes” [01:11 · patient] — transcript-supported · ⚠ needs clinician review · not validated · coding not checked — This statement concerns someone other than the patient; Cannot create a patient record for another person; Kept in the discussion ledger; no FHIR record emitted
- Continuing medicine (not a new order): “I still use albuterol” [01:35 · patient] — transcript-supported · ledger only · needs clinician review · not validated · coding not checked — Medicines require clinician review; Kept in the discussion ledger; no FHIR record emitted
- Allergy: sulfa (unconfirmed); stated reaction: rash [01:57 · patient] — transcript-supported · ⚠ needs clinician review · not validated · drug family, kept as text · needs coding review · profile-checked · coding not checked — Allergies require clinician review; A clinician must review the coding; No unambiguous local code; text retained
- Allergy denial: “I am not allergic to penicillin” [02:18 · patient] — transcript-supported · ledger only · needs clinician review · not validated · coding not checked — Allergies require clinician review; A denial requires review before any record export; Allergy denial stays in the ledger without user-supplied SNOMED; Clinician confirmation is required; Kept in the discussion ledger; no FHIR record emitted
- Ruled out: pneumonia (refuted) [03:11 · clinician] — transcript-supported · ⚠ needs clinician review · not validated · kept as text · needs coding review · profile-checked · coding not checked — A denial requires review before any record export; The ruled-out problem requires clinician confirmation; A clinician must review the coding; No unambiguous local code; text retained
- Discussed medicine (not ordered): “Amoxicillin is something we discussed, but I am not ordering amoxicillin today” [03:28 · clinician] — transcript-supported · ledger only · needs clinician review · not validated · coding not checked — Medicines require clinician review; A denial requires review before any record export; Kept in the discussion ledger; no FHIR record emitted
- DRAFT — acetaminophen — 500 mg by mouth every eight hours as needed [03:39 · clinician] — transcript-supported · ⚠ needs clinician review · not validated · profile-checked · coding not checked — Medicines require clinician review

## Discussed, not captured as records

- Symptom: “I have had a cough for four days” [00:13 · patient] — transcript-supported · ledger only · coding not checked
- Symptom: “I have not had any fever during these four days” [00:40 · patient] — transcript-supported · ledger only · needs clinician review · not validated · coding not checked
- Family history (not the patient's condition): “My father has diabetes” [01:11 · patient] — transcript-supported · ⚠ needs clinician review · not validated · coding not checked
- Continuing medicine (not a new order): “I still use albuterol” [01:35 · patient] — transcript-supported · ledger only · needs clinician review · not validated · coding not checked
- Allergy denial: “I am not allergic to penicillin” [02:18 · patient] — transcript-supported · ledger only · needs clinician review · not validated · coding not checked
- Discussed medicine (not ordered): “Amoxicillin is something we discussed, but I am not ordering amoxicillin today” [03:28 · clinician] — transcript-supported · ledger only · needs clinician review · not validated · coding not checked
