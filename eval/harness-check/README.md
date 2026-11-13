# Fictional harness-check set v1

**NOT accuracy evidence.** These 20 invented encounters validate evaluation
mechanics only. The same party wrote the transcripts and gold. There were no
independent clinicians, no blind annotations, no adjudicator disagreements,
and no measured inter-annotator agreement. No people or patient records are
represented. Medication dialogue is fictional test input, not treatment advice.
All files in this set are authored for Auris and licensed Apache-2.0.

Each encounter has six segments, supplied fictional intake, content hashes,
and hand-written gold in `manifest.json`. Gold evidence contains segment IDs,
zero-based, end-exclusive Unicode character offsets, and SHA-256 over the
exact UTF-8 span text; it contains no evidence quotes. The transcript hash is
the domain-separated canonical `Transcript.content_hash`; the intake hash is
SHA-256 over file bytes. Gold concepts use local `auris:` IDs, not new clinical
codes. Canonical UCUM units describe stated measurements without conversion.

The active extraction-v2 prompt passes the Step 4b exact-sentence,
near-duplicate and clinical-watchlist checks against all 120 segments. An
offline regression also checks that no normalized sentence occurs in the demo
transcript or any existing JSON transcript fixture. This lexical check cannot
establish absence of model pretraining exposure.

| Reduced-schema kind | Gold N |
|---|---:|
| vital_sign | 13 |
| symptom | 15 |
| clinician_assessed_problem | 6 |
| clinician_ruled_out_problem | 3 |
| patient_reported_condition | 7 |
| explicit_new_order | 3 |
| reported_medication | 7 |
| allergy_statement | 3 |
| allergy_denial | 3 |
| Total | 60 |

All kinds have N < 25 and are low-power. Two additional important facts are
explicitly out of scope: a pending diagnostic plan (`hc-12`, “rule out” does
not mean “ruled out”) and spoken oxygen flow (`hc-17`). Declared nine-kind
fact coverage is 60/62; it is a scope measure, not a recall or accuracy claim.

| Critical-error target | Encounters |
|---|---|
| Allergy denial flipped | hc-01, hc-02, hc-19 |
| Wrong drug or dose | hc-03, hc-04, hc-20 |
| Wrong person | hc-05, hc-06, hc-18 |
| Order never given | hc-07, hc-08, hc-20 |
| Denied/ruled-out condition recorded present | hc-09, hc-10, hc-19 |

These are designed opportunities to test detection, not observed model
errors. `hc-06` separates a companion speaking about the patient from a
companion's own illness. `hc-11` covers “no longer” and resolved conditions;
`hc-15` covers an explicitly outgrown allergy and a historical medication.
`hc-03` and `hc-16` cover “126 over 78” and “126/78”; `hc-16` and `hc-19`
contain multiple facts in one sentence. `hc-08` and `hc-14` include allergy
reactions, while `hc-02` is a general NKA denial. Gold terminology and
acceptable surface forms are independent of production mapping.

After the existing model, terminology and validator setup, Claude can run:

```sh
.venv/bin/auris eval run --dataset eval/harness-check/manifest.json \
  --model qwen3-8b-q4_k_m --model gemma-4-e4b-it-q4_0 --out out/eval/harness-v1
.venv/bin/auris eval score --run out/eval/harness-v1
.venv/bin/auris eval compare --run out/eval/harness-v1
```

No real inference was performed by the implementing engineer. Every harness
report carries the fictional banner, and selection always says
`not applicable: fictional harness-check set`. Passing this set does not
justify replacing the default model or publishing clinical accuracy.
