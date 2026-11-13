# Public mock-corpus candidate coverage

Each cell is candidate occurrences / encounters containing at least one.
These are lexical estimates, **not gold facts, clinical accuracy, or FHIR eligibility**.
Counts below 25 are sparse candidate evidence; even larger counts cannot establish
25 adjudicated examples. Public mock data may have appeared in model pretraining.

Counting unit: one sentence within one source turn per class, except vitals count
one per named vital type per sentence and drug mentions count longest nonoverlapping
exact token matches. Repetitions across turns count again. Questions, hypothetical
statements, negation scope, false friends and unrelated nearby numbers can inflate
counts. Cross-turn facts and unlisted wording can be missed. Source roles are used
only as lexical filters, never as adjudicated role gold. Symptoms, assessed problems
and patient history use deliberately shallow cue lists; they are not diagnoses.

Vitals require a named BP, pulse/heart rate, respiratory rate, temperature, oxygen
saturation, weight or height within eight whitespace tokens of a digit or number
word (zero through nineteen, tens, hundred). No value/unit correctness, measurement
versus history, or actual spoken dose is established. Allergy words are divided by
the existing sentence negation-cue detector; this does not prove affirmed allergy
or denial. Ruled-out and family-history phrase patterns are separate cues.
Medication names use every RxNorm term in the pinned local index plus the existing
audited UK-to-US synonyms whose target occurs in that index. Matching uses exact
normalized token sequences; no codes are assigned. New-order candidates also need
a clinician role and an order verb, with no sentence negation/family cue; reported
medications need a taking/using/on/continuing cue. Dosed-order candidates additionally
need a number followed by a dose/unit word in that sentence. This can overcount
recommendations and miss prescriptions distributed over several turns.

The primary human/scripted table excludes ACI ASR-corrected transcripts. The second
table includes all eligible canonical text to expose that distinction. ACI has 195
eligible encounters (83 human/scripted, 112 ASR-corrected); 12 training virtscribe
encounters are excluded because no named human variant is supplied. This still
provides all 20 valid encounters and 40 in each upstream test cohort. No raw ASR,
reference notes, checklists, model outputs or clinical gold are used for counts or
split selection. Coverage is computed on the full eligible pools before selecting
length strata; coverage values never drive selection.

PriMock57's UK telemedicine setting remains a limitation. The private recording
set still needs the PLAN §10 targets: at least 25 adjudicated examples each of
spoken vitals, dosed US-drug orders and affirmed allergies, with the specified
single-microphone and companion conditions. This lexical audit cannot certify any
of those targets. Human annotation and the independent FHIR subset remain pending.


## Human/scripted references

| Candidate type | primock57 all (n=57) | primock57 dev (n=17) | primock57 test (n=40) | aci-bench all (n=83) | aci-bench dev (n=10) | aci-bench test (n=29) |
|---|---:|---:|---:|---:|---:|---:|
| vital_sign | 5 / 3 | 1 / 1 | 4 / 2 | 20 / 11 | 1 / 1 | 7 / 4 |
| symptom | 381 / 49 | 131 / 17 | 250 / 32 | 350 / 73 | 40 / 8 | 127 / 25 |
| clinician_assessed_problem | 206 / 52 | 54 / 16 | 152 / 36 | 283 / 76 | 43 / 9 | 94 / 27 |
| clinician_ruled_out_problem | 5 / 5 | 2 / 2 | 3 / 3 | 7 / 7 | 1 / 1 | 1 / 1 |
| patient_reported_condition | 151 / 50 | 51 / 16 | 100 / 34 | 127 / 63 | 12 / 8 | 43 / 21 |
| explicit_new_order | 7 / 6 | 2 / 2 | 5 / 4 | 47 / 38 | 6 / 5 | 14 / 11 |
| reported_medication | 64 / 32 | 21 / 12 | 43 / 20 | 148 / 68 | 15 / 7 | 46 / 22 |
| allergy_statement | 82 / 45 | 32 / 14 | 50 / 31 | 21 / 18 | 1 / 1 | 5 / 5 |
| allergy_denial | 23 / 17 | 8 / 7 | 15 / 10 | 7 / 6 | 0 / 0 | 1 / 1 |
| family_history_cue | 63 / 27 | 17 / 7 | 46 / 20 | 21 / 14 | 1 / 1 | 10 / 6 |
| drug_name_mention | 233 / 49 | 70 / 16 | 163 / 33 | 769 / 79 | 83 / 9 | 239 / 28 |
| dosed_order_candidate | 0 / 0 | 0 / 0 | 0 / 0 | 26 / 23 | 2 / 2 | 8 / 6 |

## All canonical text (includes corrected ASR)

| Candidate type | primock57 all (n=57) | primock57 dev (n=17) | primock57 test (n=40) | aci-bench all (n=195) | aci-bench dev (n=23) | aci-bench test (n=60) |
|---|---:|---:|---:|---:|---:|---:|
| vital_sign | 5 / 3 | 1 / 1 | 4 / 2 | 147 / 62 | 17 / 9 | 37 / 20 |
| symptom | 381 / 49 | 131 / 17 | 250 / 32 | 1024 / 174 | 119 / 19 | 313 / 54 |
| clinician_assessed_problem | 206 / 52 | 54 / 16 | 152 / 36 | 636 / 181 | 79 / 20 | 197 / 57 |
| clinician_ruled_out_problem | 5 / 5 | 2 / 2 | 3 / 3 | 21 / 19 | 4 / 3 | 4 / 4 |
| patient_reported_condition | 151 / 50 | 51 / 16 | 100 / 34 | 304 / 144 | 38 / 19 | 88 / 42 |
| explicit_new_order | 7 / 6 | 2 / 2 | 5 / 4 | 66 / 55 | 8 / 7 | 21 / 18 |
| reported_medication | 64 / 32 | 21 / 12 | 43 / 20 | 341 / 149 | 37 / 18 | 112 / 44 |
| allergy_statement | 82 / 45 | 32 / 14 | 50 / 31 | 29 / 23 | 2 / 2 | 9 / 7 |
| allergy_denial | 23 / 17 | 8 / 7 | 15 / 10 | 13 / 11 | 2 / 1 | 4 / 4 |
| family_history_cue | 63 / 27 | 17 / 7 | 46 / 20 | 47 / 31 | 5 / 3 | 12 / 8 |
| drug_name_mention | 233 / 49 | 70 / 16 | 163 / 33 | 1403 / 187 | 149 / 21 | 450 / 58 |
| dosed_order_candidate | 0 / 0 | 0 / 0 | 0 / 0 | 33 / 30 | 4 / 4 | 11 / 9 |

## Provenance and reproduction

Method: `lexical_candidates_v1`; 33144 normalized drug names.
Lexicon SHA-256: `2fd6220dc2363df2446457c4f7bb28315be71511c24364a86c36f64e745f8120`.

Run `.venv/bin/python -m auris.datasets.audit` with the pinned local datasets
and terminology index. Existing manifests are verified byte for byte; differing
files are refused. Split seed 1729; see the engineering step-6c note for selection
and exclusions.
