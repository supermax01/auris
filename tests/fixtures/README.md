# Fictional fixtures

`transcript_example.json` is an entirely fictional two-speaker visit with
invented text and approximate word timings. `contrastive_cases.json`
contains fictional mini-transcripts and expected reduced assertions,
material flags, and review reasons. Neither file describes a real person,
visit, measurement, prescription, or allergy.

The contrastive cases are a development **smoke test**, not an accuracy
benchmark or the locked PLAN A9 evaluation. The active extraction prompt must
pass the leakage guard against all fixture transcript texts before inference.
Keep new clinical names out of prompt examples and extend the guard's clinical
watchlist when adding new fixture content. V1's contaminated examples are
retained only for reproducibility; v2 is the active prompt.

The other fixture, `fake_structure_definition.json`, is a small synthetic
definition for checklist tests, not an authoritative FHIR definition.
