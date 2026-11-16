# Examples

Everything in this folder is **fictional example data** written for the Auris
demo and tests. It describes no real person, visit or organisation. Never
replace it with real patient data.

| File | What it is |
|---|---|
| `transcript.json` | A scripted four-minute primary-care visit between a clinician and a patient. The times are invented, not measured from audio. It includes every kind of fact Auris handles: symptoms, vital signs, an allergy and an allergy denial, an assessed and a ruled-out condition, a family history, a continuing medicine, a medicine discussed but not prescribed, and a new prescription. |
| `intake.json` | The visit details Auris needs to build records: the patient, the clinician and the visit date. Identifiers use `example.org`, and every name and value is marked fictional. |

## Run the demo

After [setup](../README.md#quick-start), from the repository root:

```sh
uv run auris demo
```

This writes the visit note and records to `./out/demo/`. The folder must be new
or empty. To run the same visit into another folder:

```sh
uv run auris run --transcript examples/transcript.json \
  --intake examples/intake.json --out out/another-demo
```

Output folders are created readable only by your user account.

## File formats

**Transcript.** A list of segments, each with an id, a speaker, the speaker's
role (`clinician`, `patient`, `companion` or `other`), start and end times in
seconds, and the text. `auris transcribe` produces this format from audio.

**Visit details (intake).** A small input form, not a full FHIR resource: the
patient's name, sex and identifier, the clinician's name and identifier,
and the visit's type and start and end times. Dates keep the precision and time zone
you provide. Auris turns them into the Patient, Practitioner and Encounter
records that every exported record refers to.

## Demo expectations

The expected results for this visit are in
[`tests/fixtures/demo/expected.json`](../tests/fixtures/demo/expected.json), with
the exact visit note beside it. They are a regression test, not a measure of
accuracy. Until a clinician approves the facts, the export contains only the
patient, clinician and visit records, and every fact stays visible in the note
for review.
