# Recorded official validator output — fictional data only

These are unmodified OperationOutcome Bundles recorded on 2026-11-12 from
the pinned HL7 validator 7.0.5, FHIR R4 4.0.1, US Core 6.1.0. Both logical
passes run in one JVM, with terminology and HTTP access disabled.

- `golden-outcomes.json`: the 13 resource-bearing Step 2b golden cases,
  in their existing order, followed by Patient, Practitioner and Encounter
  from the fictional demo intake (2026-11-02). All pass both structural lanes.
- `broken-outcomes.json`: six mutations in `tests/validator_helpers.py`,
  in this order: allergy missing clinicalStatus; BP root value with no
  components; BP missing systolic; medication request missing intent;
  Condition missing category; heart rate missing effectiveDateTime.

Each resource produces `NNNN-base.json` then `NNNN-profile.json` input
identities. The first removes `meta.profile`; the second preserves it.
Input-file extensions use relative filenames, so these fixtures contain
no personal filesystem paths. Narratives and diagnostics are the official
validator's output, not hand-authored errors. Clinical examples are fictional;
terminology and FHIR constraints come from the pinned packages.

The runner uses `-allow-example-urls true` explicitly for these fixtures
because the fictional intake identifiers use example.org. Production callers
default to false. Full commands, timing and lane caveats are documented in
[`docs/engineering/step-3.md`](../../../docs/engineering/step-3.md).
