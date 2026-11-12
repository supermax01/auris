# FHIR reference for Auris — R4 4.0.1 + US Core 6.1.0

Why R4, not R5: US Core 6.1.0 (`hl7.fhir.us.core#6.1.0`, "based on FHIR
4.0.1") is the profile set Auris pins, and US Core is R4-only. The
hl7.org/fhir root now serves R5; always use the `/fhir/R4/` and
`/fhir/us/core/STU6.1/` pages. (US Core pages note 9.0.0 supersedes 6.1.0;
6.1.0 is kept for USCDI v3 compatibility and is configurable.)

Facts below were read from the official pages on 2026-11-10. Severity
"error" = validation failure; "best practice"/"guideline" = warning.
Items marked *(summary)* came from the unread tail of a long page and must
be confirmed against the StructureDefinition JSON in Slice 1 task zero.

## Resources Auris touches

| Role | Resource | US Core 6.1.0 profile | Auris emits? |
|---|---|---|---|
| Clinical output | Observation | us-core-vital-signs + specific vital profiles (blood-pressure, heart-rate, …) | Yes |
| Clinical output | Condition | us-core-condition-encounter-diagnosis; us-core-condition-problems-health-concerns | Yes |
| Clinical output | MedicationRequest | us-core-medicationrequest | Yes (draft/proposal only) |
| Clinical output | AllergyIntolerance | us-core-allergyintolerance | Yes |
| Intake anchor | Patient | us-core-patient | Supplied, never extracted |
| Intake anchor | Practitioner | us-core-practitioner | Supplied, never extracted |
| Intake anchor | Encounter | us-core-encounter | Supplied, never extracted |
| Transport | Bundle (transaction) | — | Integration proof only |
| Not in v1 | Provenance, MedicationStatement, CarePlan, ServiceRequest | — | No |

## Observation — vital signs

**Required (min ≥ 1), US Core vital-signs base:** `status` 1..1 (required
binding ObservationStatus); `category` 1..* with slice `VSCat` 1..1 —
`coding.system` fixed `http://terminology.hl7.org/CodeSystem/observation-category`,
`coding.code` fixed `vital-signs`; `code` 1..1 (extensible, US Core Vital
Signs VS); `subject` 1..1 Reference(US Core Patient); `effective[x]` 1..1.

**Status:** ObservationStatus = registered | preliminary | final | amended |
corrected | cancelled | entered-in-error | unknown. Profiles do not narrow
it, so `preliminary` is allowed.

**Blood pressure (us-core-blood-pressure):** `code` fixed LOINC `85354-9`.
`component` 2..*: slice `systolic` 1..1 (`8480-6`), slice `diastolic` 1..1
(`8462-4`). Component `valueQuantity`: `system` fixed
`http://unitsofmeasure.org`, `code` fixed `mm[Hg]`, `unit` and `value`
required when present. Root `value[x]` is allowed by cardinality but the
profile says it **SHOULD be omitted**. A missing systolic or diastolic
value **SHALL** carry `component.dataAbsentReason`.

**Heart rate (us-core-heart-rate):** `code` LOINC `8867-4`; UCUM `/min`.

**effective[x]:** 1..1; US Core BP/HR list only `effectiveDateTime`.
`vs-1` (error): "if Observation.effective[x] is dateTime and has a value
then that value shall be precise to the day" — `($this as dateTime).toString().length() >= 8`.

**Other invariants (error):** `vs-2` — no component/hasMember ⇒ value[x] or
dataAbsentReason; `vs-3` — each component has value or dataAbsentReason;
`obs-6` — dataAbsentReason only if value absent; `obs-7` — no value under a
code duplicated in a component.

**Bindings:** category preferred; code extensible; value units extensible
to VitalSignsUnits (base R4 component units were required; US Core relaxes
to extensible).

**Auris consequences:** mapper always injects the VSCat category and the
fixed LOINC/UCUM codes; BP never uses root value; a vital with one BP
component missing, a mismatched unit, or no spoken unit where the profile
does not fix exactly one goes to review (BP and HR fix one, so the mapper
supplies it; Auris never writes dataAbsentReason to paper over a missing
spoken value); time is
inherited from `Encounter.period.start` only for in-visit measurements.

## Condition

**R4 base:** only `subject` 1..1. `clinicalStatus` 0..1 (required:
active | recurrence | relapse | inactive | remission | resolved);
`verificationStatus` 0..1 (required: unconfirmed | provisional |
differential | confirmed | refuted | entered-in-error).

**Invariants:** `con-3` (guideline / US Core "best practice"):
clinicalStatus SHALL be present if verificationStatus ≠ entered-in-error
and category is problem-list-item. `con-4` (error): if abated, clinicalStatus
must be inactive, resolved or remission. `con-5` (error): no clinicalStatus
when entered-in-error. US Core 6.1.0 adds **no** us-core-* invariant and keeps
clinicalStatus 0..1 in both profiles.

**us-core-condition-encounter-diagnosis:** `category` 1..*, slice `us-core`
1..1 fixed `condition-category#encounter-diagnosis`; `code` 1..1 (extensible,
US Core Condition Codes; "ICD-10-CM … SHOULD be used as the primary code for
current encounter diagnoses"); `subject` 1..1. `encounter` 0..1 Must Support —
"The encounter SHOULD always be referenced".

**us-core-condition-problems-health-concerns:** `category:us-core` 1..*,
required binding to problem-list-item | health-concern; optional
`category:screening-assessment`; `code` 1..1; `subject` 1..1. Prose:
problem-list-item ⇒ clinicalStatus **SHOULD** be present.

**Must Support (both):** clinicalStatus, verificationStatus, category, code,
subject, onsetDateTime, abatementDateTime, recordedDate, assertedDate
extension; encounter (encounter-diagnosis).

**Auris consequences:** set clinicalStatus from explicit temporality only;
omit it on refuted encounter-diagnoses (0..1 allows it; condition-clinical
has no state for a ruled-out condition); never infer onset/abatement;
always reference the intake Encounter; text-only `code` permitted under the
extensible binding but flagged for coding review.

## MedicationRequest

**Required (min ≥ 1):** `status` 1..1 (required: active | on-hold |
cancelled | completed | entered-in-error | stopped | **draft** | unknown);
`intent` 1..1 (required: **proposal** | plan | order | original-order |
reflex-order | filler-order | instance-order | option); `medication[x]` 1..1;
`subject` 1..1 Reference(US Core Patient).

**requester:** **0..1** Must Support (not 1..1; also `authoredOn`, `reported[x]`,
`encounter` are 0..1 — confirmed against the StructureDefinition JSON via
Firecrawl). `us-core-21` (error):
"requester SHALL be present if intent is "order"" — fires only for the order
family, **not** for `proposal`. Requiring requester on proposals is Auris
policy, not a profile rule.

**medication[x]:** CodeableConcept or Reference(US Core Medication);
extensible binding to VSAC Medication Clinical Drug (2.16.840.1.113762.1.4.1010.4)
— "When using a code, RXNorm concepts are used". Text-only is permitted only
by the generic extensible rule (concept not covered by the value set).

**Must Support:** status, intent, category:us-core, reported[x], medication[x],
subject, encounter, authoredOn (0..1), requester, dosageInstruction (text,
timing, doseAndRate/dose[x]), dispenseRequest (numberOfRepeatsAllowed,
quantity), reasonCode/reasonReference *(summary)*. No element under
`dosageInstruction` is min ≥ 1, so text-only dosage is valid; R4 says text
"will always be populated".

**Auris consequences:** `status=draft`, `intent=proposal`; requester bound to
the identified initiating clinician confirmed against intake (ambiguous →
review); `authoredOn` inherits `Encounter.period.start` with a provenance note;
`encounter` = intake Encounter; `reportedBoolean=false` for in-visit orders;
dose text verbatim or absent.

## AllergyIntolerance

**Required (min ≥ 1), US Core:** `code` 1..1; `patient` 1..1; and if `reaction`
is present, `reaction.manifestation` 1..*.

**clinicalStatus** 0..1, required binding: active | inactive | resolved.
**verificationStatus** 0..1, required: unconfirmed | confirmed | refuted |
entered-in-error.

**Invariants (error):** `ait-1` — "AllergyIntolerance.clinicalStatus SHALL be
present if verificationStatus is not entered-in-error." `ait-2` — no
clinicalStatus when entered-in-error. **So every exported Auris allergy needs
clinicalStatus.**

**code:** extensible to VSAC "Common substances for allergy and intolerance
documentation including refutations" (2.16.840.1.113762.1.4.1186.8); text
only under the generic extensible rule.

**No Known Allergies (US Core guidance):** asked and reports none ⇒
`code` = 716186003 "No known allergy (situation)" with
`verificationStatus=confirmed` (not `refuted`), and `clinicalStatus` still
required by ait-1; not asked ⇒ `code` =
1631000175102 "Patient not asked". Both are SNOMED CT concepts (code system
not named on the page), so tier 0 (no SNOMED) cannot emit them.

**type** 0..1 (allergy | intolerance — omit if undetermined); **category**
0..* (food | medication | environment | biologic).

**Auris consequences:** `clinicalStatus=active` + `verificationStatus=unconfirmed`
for an affirmed allergy; explicitly outgrown/resolved → review; NKA only with
user-supplied SNOMED and clinician confirmation, otherwise ledger; `type`
omitted unless stated; reaction only when a manifestation is stated;
drug-family allergens (penicillin, sulfa) are never mapped to a single
ingredient and stay text-only with `uncoded_text_only`.

## Intake anchors (minimum valid US Core resources)

- **Patient:** `identifier` 1..* (system + value 1..1); `name` 1..* with
  `us-core-6` (family and/or given, or data-absent-reason); `gender` 1..1
  (male | female | other | unknown).
- **Practitioner:** `identifier` 1..* (system + value); `name` 1..* with
  `name.family` 1..1. NPI slice optional (us-core-16/17 apply only if used).
- **Encounter:** `status` 1..1; `class` 1..1 (ActEncounterCode, extensible);
  `type` 1..* (US Core Encounter Type, extensible); `subject` 1..1; `period`
  Must Support (Auris requires `period.start`).

## Transaction Bundle

`entry.request` mandatory for transaction (`method` + `url` 1..1);
`ifNoneExist` = query portion only. fullUrl unique, not version-specific,
must not disagree with resource id. Conditional update `PUT [type]?[params]`:
0 matches → create, 1 → update, >1 → 412 (conditional ops are trial use).
*(summary)* Processing order DELETE → POST → PUT/PATCH → GET; all-or-nothing;
POST urn:uuid fullUrls get server ids and in-bundle references are rewritten.
Auris uses client-assigned ids with `PUT` so reruns are idempotent.

## Provenance (why it is out of v1)

US Core Provenance needs `target` 1..*, `recorded` 1..1, `agent` 1..* with
`who` 1..1, and `provenance-1`: onBehalfOf SHALL be present when agent.who is
a Practitioner — i.e. an Organization resource too. Review sign-off lives in
a local append-only review log instead.

## Sources

- https://hl7.org/fhir/R4/observation.html, https://hl7.org/fhir/R4/vitalsigns.html, https://hl7.org/fhir/R4/valueset-observation-status.html
- https://hl7.org/fhir/R4/condition.html
- https://hl7.org/fhir/R4/medicationrequest.html
- https://hl7.org/fhir/R4/allergyintolerance.html
- https://hl7.org/fhir/R4/bundle.html, https://hl7.org/fhir/R4/http.html
- https://hl7.org/fhir/us/core/STU6.1/StructureDefinition-us-core-vital-signs.html
- https://hl7.org/fhir/us/core/STU6.1/StructureDefinition-us-core-blood-pressure.html
- https://hl7.org/fhir/us/core/STU6.1/StructureDefinition-us-core-heart-rate.html
- https://hl7.org/fhir/us/core/STU6.1/StructureDefinition-us-core-condition-encounter-diagnosis.html
- https://hl7.org/fhir/us/core/STU6.1/StructureDefinition-us-core-condition-problems-health-concerns.html
- https://hl7.org/fhir/us/core/STU6.1/StructureDefinition-us-core-medicationrequest.html
- https://hl7.org/fhir/us/core/STU6.1/StructureDefinition-us-core-allergyintolerance.html
- https://hl7.org/fhir/us/core/STU6.1/StructureDefinition-us-core-patient.html
- https://hl7.org/fhir/us/core/STU6.1/StructureDefinition-us-core-practitioner.html
- https://hl7.org/fhir/us/core/STU6.1/StructureDefinition-us-core-encounter.html
- https://hl7.org/fhir/us/core/STU6.1/StructureDefinition-us-core-provenance.html
