# Step 2b: terminology and the A3 mapper

Historical step report: [Step 3](step-3.md) replaces the mapper-only
`final_exportable` state below with computed validation/review gating and
moves the fictional demo encounter into the pinned ICD fiscal year.

`auris setup terminology` verifies both archives against frozen SHA-256
pins (and the published RxNorm MD5), then atomically rebuilds
`cache/terminology/index.sqlite`. Missing archives produce their URLs and
an explicit `auris setup terminology --fetch` command. Only that opt-in
setup path fetches; the mapper and tests never fetch. The SQLite index
records source pins and the ICD effective interval. It is rebuildable,
gitignored, and opened read-only during mapping.

`TerminologyIndex.exact(text, system)` returns one unambiguous assignable
code or None. `candidates(text, system, k)` ranks by token-set Jaccard
similarity with deterministic ties. The mapper never calls `candidates`.
FTS5 is detected during indexing; a scan provides the same ranking if
FTS5 is unavailable, including when moving an index to another runtime.
Numeric punctuation remains significant in exact matching.

Construct `Mapper(intake, transcript, terminology, unit_sets)`, then call
`map_assertion(assertion, position, evidence=None)` or `map_all(assertions)`.
Obtain `unit_sets` using `load_unit_sets(core_package, expected_sha256)`;
this reads the three actual closed UCUM value sets from the verified R4
cache. Fixed profile values come directly from task zero's checklist.
Tests use small extracts through the same parsers and need no cache.

Each MappingOutcome carries a resource or a complete assertion ledger
entry, claimed profile (None for intentional ledger rows), aligned evidence,
review reasons, mapper flags, materiality, provenance notes and five lanes.
Evidence results supplied by a caller must match this transcript/assertion.
`final_exportable` means eligible at the mapper stage, not validated;
the R4, US Core and terminology lanes are NOT_CHECKED in this step.
`build_collection` includes eligible resources and the unchanged clinical
intake anchors, serializing local references as resolvable urn:uuid links.
Unresolved material mappings or material lane failures block the default
collection. Intentional, evidence-passing ledger rows do not block it.
`preview=True` includes built review resources. Refused mappings never
produce incomplete FHIR resources, even in preview. Bundle writes use the
existing private-storage helper (0600 file, 0700 directory).

## A3 choices and limits

- Added `Temporality.RESOLVED` to the reduced form. A3 needs an explicit
  resolved state; `past` cannot establish resolution. Resolved conditions
  get clinicalStatus=resolved and historical review. Past conditions omit
  clinicalStatus and require review. Onset/abatement remain absent because
  step 2a supplies neither field. Allergy type likewise remains absent.
- Refused resource mappings retain all extracted text in the ledger with
  needs_review; intentional A3 ledger rows use ledger_only. Family/other
  and unknown experiencers never acquire the intake patient's identity.
  A clinician classification must be supported by a cited clinician turn.
- One identified clinician speaker cluster is bound to the single intake
  Practitioner. Multiple clinician clusters refuse prescriber selection.
  A future/current, affirmed new_order classification is required. Dose
  text is copied verbatim; separate route/frequency fields are not joined
  into new prose or converted into structured dosage.
- Respiratory rate and pulse oximetry also fix one UCUM unit in the actual
  checklist; the mapper supplies these, as it does for BP/HR. SpO2 emits
  both required LOINC codings and no components. Separate oxygen-flow or
  oxygen-concentration assertions go to the ledger. Compound/unparseable
  measurement values are retained for review rather than split or guessed.
- In-visit time inheritance requires current temporality and a clinician
  source. Reported/past readings require an explicit day-precision date.
  ISO R4 dates/timestamps and unambiguous English month-name dates are
  supported. Relative dates, ambiguous numeric dates and low precision
  dates require review. No dates, timezones or measurement units are
  inferred beyond the A3 inheritance/fixed-unit rules.
- PIN/BN names map to an IN only through an explicit, unambiguous RXNREL
  relationship; no ingredient is guessed from a name. The CPC archive's
  blank RXNREL SUPPRESS field is accepted as well as N. RXNCONSO still
  requires SUPPRESS=N and SAB=RXNORM exactly.
- The seven requested UK/US synonym pairs are recorded as whole-name
  aliases. Six resolve to IN codes. Co-amoxiclav resolves to the name
  amoxicillin / clavulanate, whose CPC TTY is MIN, outside the requested
  IN/PIN/BN/SCD/SBD auto-coding scope. It therefore stays text-only with
  needs_coding_review; neither a single ingredient nor a combination code
  is guessed. Supporting MIN requires a later contract decision.
- ICD-10-CM assignment requires the encounter's supplied date to lie in
  FY2027. Out-of-period/insufficient-precision encounters retain text and
  require coding review. In particular, the original January 2026 demo
  intake predates this terminology release; it was left unchanged.
- `uncoded_text_only` is exposed as a mapper flag and review reason;
  `needs_coding_review` is also present. None of these claims terminology
  validation. Reaction manifestations are verbatim text at tier zero.
- Checklist conformance treats required children of absent optional
  backbones as conditional, as element_sources.toml specifies. Every
  built test resource is checked for required, fixed and pattern elements;
  mutation tests prove missing slices and changed profiles fail the check.

Follow-ups belong to later steps: the official validator lanes, review
approval/dismissal and run-wide revalidation, transaction PUT integration,
and any extension of extraction to explicit onset/abatement. No network
fetch was exercised here; the fetch contract is covered with fake fetchers.
No Python dependency was added.
