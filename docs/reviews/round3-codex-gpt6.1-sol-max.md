# Round-3 review: Codex (gpt-6.1-sol, reasoning max)

Run headless via `codex exec -s read-only` on 2026-11-10 against PLAN.md + round3-claude-opus-5.5.md. Output recorded verbatim.

**1. Verdict — AMEND overall:** The review identifies real blockers, but several proposed fixes still infer clinical facts or leave acceptance criteria unresolved.

**2. Amendments**

- **A1 — EDIT:** “Set `clinicalStatus=active` for explicitly current allergy risk. Historical reactions do not establish resolved risk. Outgrown/resolved or unclear risk goes to clinician review; export requires an evidence-supported clinical status.”

- **A2 — EDIT:** “For both Condition rows, explicitly current → `active`; explicitly resolved → `resolved`; historical or unclear → review. Speaker identity never determines clinical status. Populate `abatement[x]` only from explicit evidence.”

- **A3 — ACCEPT.**

- **A4 — EDIT:** “Freeze PriMock57 at 17 dev/40 test; ACI-Bench at 23 dev (20 validation + 3 training)/60 test. Commission 10 private exploratory test encounters. Manifest audio/text denominators separately. Proposed annotation budget: `65 + 2.4×Ntest + 1.5×Ndev` hours: full ≈389h; reduced 60-test ≈269h. Include private annotation; add $3,000 recording allowance. Pilot-reprice all estimates.”

- **A5 — EDIT:** “Use those 40 development encounters. Keep 8B provisional; select a larger model for ≥3 F1 points improvement at equal coverage, without increased critical errors, within hardware budgets. Report paired encounter-bootstrap 95% CIs; CI exclusion is not the selection gate. Release requires A8.”

- **A6 — EDIT:** “Laptop candidate: [Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo), converted to CTranslate2 INT8; extractor: self-converted Qwen3-8B GGUF Q4_K_M, `n_ctx=8192`, upstream non-thinking chat template. Slice 1 freezes source/runtime revisions, conversion commands, template hash and artifact checksums. Reference/minimum: 16GB M2 MacBook Air, pipeline memory ≤12GiB; Linux reference: Ubuntu 24.04, RTX3090 24GB, 64GB RAM.”

- **A7 — EDIT:** “Use the pinned [HL7 validator-wrapper](https://github.com/hapifhir/org.hl7.fhir.validator-wrapper) as a persistent loopback service, Temurin 21 via mise. Load ASR → diariser → extractor sequentially, unloading before the next stage; then validate. Use whole encounters when they fit; otherwise 4,096-token windows with 256-token overlap and global offsets. Deduplicate identical assertions with overlapping evidence; conflicts require review.”

- **A8 — EDIT:** “Slice 1: all matrix fixtures and expected refusals pass; offline CI passes. Slice 2: role accuracy ≥95% at ≥90% turn coverage; assertion F1 ≥0.80 and ≤5-point loss versus oracle roles. Slice 3: frozen predictions reproduce identical metrics; regenerated public F1 differs ≤1 point. Release: per-class recall ≥0.80, ≥25 gold assertions/class, zero observed critical errors before reviewer edits; laptop wall time ≤30 minutes/10-minute audio.”

- **A9 — EDIT:** “Tier-0 final export requires clinician signoff and all non-terminology lanes PASS; label ‘structurally checked; terminology NOT_CHECKED’, without full US Core conformance claims. Review cannot waive validation failures. Authenticated clinicians resolve material items; Provenance records identity, time and target version. Edits invalidate approval.”

- **A10 — ACCEPT.**

- **A11 — ACCEPT**, as provenance clarification; “all four reviewed” is already compatible with Gemini contributing only round 2.

**3. Missed defects**

- **Historical vitals become current.** PLAN §3 unconditionally inherits encounter start, although `effective[x]` describes when the measurement was physiologically relevant. Amendment: “Inherit encounter date only for explicitly in-visit measurements; preserve known temporal precision. Historical or temporally ambiguous measurements require evidence-supported time or review.” [R4 Observation](https://hl7.org/fhir/R4/observation-definitions.html#Observation.effective_x_)

- **Severity cannot identify validation lanes.** PLAN says lanes are parsed from OperationOutcome severities; severity describes importance, while code/details/expression describe the issue. Amendment: “Determine lanes from explicit checker scope and issue metadata; skipped or unsupported checks are NOT_CHECKED. Severity alone never establishes which lane passed.” [OperationOutcome](https://hl7.org/fhir/R4/operationoutcome-definitions.html)

- **Default requester can misattribute prescribing.** PLAN binds every request to an intake Practitioner without establishing who initiated it. US Core 6.1.0 requester is `0..1`, conditionally required for order intents—not universally `1..1`. Amendment: “Bind requester to the identified initiating clinician, confirmed against supplied identity; ambiguity requires review. Requiring requester for proposals is Auris policy.” [MedicationRequest profile](https://hl7.org/fhir/us/core/STU6.1/StructureDefinition-us-core-medicationrequest.html)

- **Terminology licensing remains underspecified.** “Small table + notice” omits distribution placement/display requirements; UCUM obligations are unnamed. Amendment: “Ship `LOINC_short_license.txt`, required download-page attribution and associated official displays; inventory UCUM artifacts and satisfy their applicable licence/attribution conditions.” [LOINC licence](https://loinc.org/kb/license), [UCUM licence](https://ucum.org/license)

- **ACI speaker tags are not oracle roles.** Its README explicitly documents swapped doctor/patient tags. Amendment: “Independently adjudicate speaker/role gold from available audio; otherwise exclude affected items from oracle-role and role-accuracy claims. Include this work in pilot timing.” [ACI-Bench](https://github.com/wyim/aci-bench)

- **Transaction mechanics and retry behaviour remain undefined.** `urn:uuid` references alone do not satisfy transaction requests. Amendment: “Every entry supplies `request.method/url`; the local proof uses stable client IDs and PUT requests. Submitting twice must create no additional resources or broken references.” [R4 Bundle](https://hl7.org/fhir/R4/bundle-definitions.html)

**4. Changes that would worsen the plan**

A1 incorrectly treats historical reactions as incompatible with active risk; A2 treats historical disease as resolved and clinician assessment as active. A5’s 40-encounter alternative does not establish statistical power; D5’s “unreachable” claim is unsupported. A8’s “within CI” is a weak reproducibility criterion. A9 must preserve mandatory clinician review and prohibit approval from overriding failed validation.