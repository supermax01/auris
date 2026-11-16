# Visit note

**Fictional demo: no real patient data. Times are scripted; there is no recording.**

**10 items need review**, marked ⚠ or “To check” below.

Export file: the patient, clinician and visit only; no records yet. Export waits until a clinician approves or dismisses every item.

## Subjective

_Symptoms and denials stay in this note; they are not exported._

- Symptom: “I have had a cough for four days” (patient, 0:13) — note only
- Symptom: “No fever” (patient, 0:40) — note only · needs review
  - To check: A denial requires review before any record export
- Allergy: sulfa (unconfirmed); stated reaction: rash (patient, 1:57) — ⚠ needs review · drug family, kept as words · checked against US Core
  - To check: Allergies require clinician review; A clinician must review the coding; No unambiguous local code; text retained
- Allergy denial: “I am not allergic to penicillin” (patient, 2:18) — note only · needs review
  - To check: Allergies require clinician review; A denial requires review before any record export; The installed code set cannot represent this allergy denial as a record; Clinician confirmation is required

## Objective

- Blood pressure 126/78 mmHg (clinician, 2:36) — awaiting approval · checked against US Core
- Heart rate 84 /min (clinician, 2:44) — awaiting approval · checked against US Core

## Assessment

- Patient-reported: asthma (unconfirmed, active) (patient, 1:01) — ⚠ needs review · kept as words, no code · checked against US Core
  - To check: A clinician must review the coding; No unambiguous local code; text retained
- Family history (not the patient's condition): “My father has diabetes” (patient, 1:11) — ⚠ needs review
  - To check: This statement concerns someone other than the patient; Cannot create a patient record for another person
- Assessed: acute bronchitis (provisional, active) (clinician, 2:58) — ⚠ needs review · kept as words, no code · checked against US Core
  - To check: A clinician must review the coding; No unambiguous local code; text retained
- Ruled out: pneumonia (refuted) (clinician, 3:11) — ⚠ needs review · kept as words, no code · checked against US Core
  - To check: A denial requires review before any record export; The ruled-out problem requires clinician confirmation; A clinician must review the coding; No unambiguous local code; text retained

## Plan

- Draft order: acetaminophen — 500 milligrams by mouth every eight hours as needed (clinician, 3:39) — ⚠ needs review · checked against US Core
  - To check: Medicines require clinician review

## Also discussed

_Kept in this note, not exported as records._

- Continuing medicine (not a new order): “I still use albuterol” (patient, 1:35) — note only · needs review
  - To check: Medicines require clinician review
- Discussed medicine (not ordered): “We could try amoxicillin, but I don't think you need an antibiotic” (clinician, 3:28) — note only · needs review
  - To check: Medicines require clinician review; A denial requires review before any record export

---

Written by Auris from the visit transcript. Every item quotes what was said, by whom and when. Times are from the transcript and approximate. Records are checked against US Core; medical codes are not checked against a terminology server.

Model qwen3-8b-q4\_k\_m · prompt extraction\_v2 · written 2026-11-12T12:00:00+00:00. Research software, not cleared or approved for clinical use; clinician review mandatory. Regulatory classification is deployment-specific.
