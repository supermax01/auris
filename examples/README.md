# Examples

Everything in this directory is **fictional example data**, written for
the Auris demo and tests. It describes no real person, visit or
organisation, and must never be replaced with real patient data.

- `intake.json` — the fictional intake anchors (Patient, Practitioner,
  Encounter) for the demo run, at the PLAN Appendix A1 minimums.
  Identifier systems use `example.org` and every name and value is
  labelled fictional.
- `transcript.json` — a scripted 4 minute 10 second primary-care visit,
  with a clinician and patient. Times are invented segment bounds, not
  measured audio. It covers all nine A3 rows, including a family-history
  condition, a continuing medicine, a medicine discussed without an order,
  a sulfa-family allergy and a specific allergy denial. The medicine
  dialogue is fictional test content, not treatment advice.

After setup, run `auris demo` from the repository root. It writes
`extraction.json`, `validation.json`, `bundle.json` and `note.md` to
`./out/demo/` (0700 directory, 0600 files). The directory must be new or
empty. Use `auris run --transcript examples/transcript.json --intake
examples/intake.json --out out/another-demo` for a separate run.

The hand-written replay and expected outcomes live in
[`tests/fixtures/demo/expected.json`](../tests/fixtures/demo/expected.json),
with an exact note snapshot beside them. They are a regression/smoke
fixture, not adjudicated clinical gold or an accuracy benchmark. The demo
passes the active prompt's leakage guard with a demo-specific clinical
watchlist. The default tier-zero path never claims coding validation.
Unresolved material assertions block final clinical export, so the
hand-written demo replay produces an anchors-only collection. All extracted
facts and review reasons remain visible in the note and JSON artifacts.

The intake JSON is a small Auris input form, not a complete FHIR resource
Bundle. Encounter subjects currently use the local `Patient/<id>` form;
if an optional patient id is supplied, it must match that reference.
Dates retain the precision and timezone provided by the operator.
The later FHIR validator remains responsible for full resource validation.

Transcript and intake `save()` methods write at mode `0600` inside a
`0700` workdir (PLAN A8). They create the immediate workdir if missing and
refuse an existing directory with broader permissions.

Fictional transcript and contrastive fixtures are documented in
[`tests/fixtures/README.md`](../tests/fixtures/README.md).
