# Examples

Everything in this directory is **fictional example data**, written for
the Auris demo and tests. It describes no real person, visit or
organisation, and must never be replaced with real patient data.

- `intake.json` — the fictional intake anchors (Patient, Practitioner,
  Encounter) for the demo run, at the PLAN Appendix A1 minimums.
  Identifier systems use `example.org` and every name and value is
  labelled fictional.

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
