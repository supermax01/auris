### 1. Verdict: BLOCK

**Strongest Reason:** §3's Export Matrix contains a structurally impossible precondition for audio-derived Vital Sign Observations under US Core 6.1.0, and an unhandled negation trap across Condition and AllergyIntolerance that will guarantee either 0% vital-sign export or invented metadata.

Specifically:

1. **The Vital Signs Timestamp Trap (vs-1):** §3 states that a Vital Sign Observation is exported only "with value+unit+time". Under hl7.fhir.us.core#6.1.0, all vital sign profiles inherit constraint vs-1 (effectiveDateTime or effectivePeriod is 1..1 mandatory, and if dateTime is used, it must be at least to the day: YYYY-MM-DD). Spoken ambient audio almost never contains a calendar date when a nurse or doctor says "BP is 128 over 82, heart rate 74". Because the Extraction rules strictly state "The LLM... never fills missing facts", an extractor operating on audio/transcript alone will always output time=null for vitals, meaning zero vital signs will ever pass the US Core lane unless the mapper deterministically inherits the timestamp from the intake Encounter.period.start (which §3 does not authorize). Furthermore, Blood Pressure (us-core-blood-pressure) is a component-based panel (85354-9) where Observation.value[x] is forbidden and values live in component slices (8480-6 / 8462-4)—a generic "value+unit+time" row breaks on the single most common vital sign in primary care.
   FHIR specification - FHIR.org + 1

2. **The Negation & Refutation Contradiction:** Under Approach, the extractor emits a negation field, and §3 declares any negated assertion a "Material assertion" (where unresolved ⇒ export blocked). Yet §3 sets fixed semantics of verificationStatus=provisional / unconfirmed for Condition and verificationStatus=unconfirmed for AllergyIntolerance. If a patient states "I am NOT allergic to penicillin" or a clinician states "Ruled out pneumonia", exporting that with verificationStatus=unconfirmed or provisional inverts the clinical meaning into a positive suspected allergy/condition! Conversely, if negated assertions are marked "unresolved" and blocked from export, then every clinical pertinent negative or allergy refutation in an encounter permanently blocks final export.
   HL7 International

### 2. What Claude, Codex, and Muse Missed

#### A. Architecture & FHIR Mechanics

- **Transaction Bundle Reference Integrity (urn:uuid: vs Server IDs):** §3 notes that "Patient/Encounter (and optional Practitioner) anchors are supplied intake context" and requires a "transaction exporter against a local test server". In a FHIR R4 transaction Bundle, every entry requires a request block (method + url), and internal cross-references must resolve via urn:uuid:<uuid> fullUrls or pre-existing server relative URLs (Patient/123). If intake context supplies only an external identifier or bare stub without defining whether the Patient and Encounter are PUT/POST entries inside the transaction Bundle vs. pre-seeded on the server, the HAPI/test server transaction will fail on referential integrity (400 Bad Request).

- **MedicationRequest requester (1..1 Must-Support in US Core 6.1.0):** In us-core-medicationrequest (6.1.0), requester is 1..1 mandatory. Yet §3 says "Patient/Encounter (and optional Practitioner) anchors are supplied intake context". If Practitioner is optional in intake context, any MedicationRequest will deterministically fail US Core profile validation on missing requester.

- **Mechanical Verbatim Quote Check vs. Whisper Punctuation/ITN:** The plan mandates a "mandatory verbatim quote (mechanically checked against the transcript)". In practice, faster-whisper inverse text normalization (ITN) and punctuation formatting produce tokens like "120/80 mmHg" or "500mg", whereas an LLM—even at temperature=0 under XGrammar/llama.cpp—frequently normalizes whitespace or casing in string fields, causing naive exact-substring checks to fail at a 10–25% rate and triggering false repair loops. You need normalized token-span alignment (case-folded, whitespace/punctuation-stripped character offset projection back to the immutable transcript), or better yet, have the LLM emit transcript sentence/turn IDs + token spans verified by fuzzy Levenshtein/token overlap thresholds.

- **Mac vs. Linux Constrained Decoding Divergence:** Claiming "Backend contract tests must prove identical schema enforcement" between llama.cpp (GBNF grammars) and vLLM + XGrammar (pushdown automata over tokenizer vocabularies) is a trap. Even with identical JSON schemas and temperature=0, llama.cpp GBNF and XGrammar handle whitespace tokens, key ordering, and floating-point hardware reductions (Apple Metal vs. CUDA/CPU AVX) differently—they will not produce bit-identical token streams. Contract tests must assert AST-level structural equivalence (both strictly conform to the Pydantic/JSON schema and reject identical invalid outputs), not identical generation outputs.

- **HL7 Validator CLI Daemon Protocol:** The official validator_cli.jar does not ship a well-documented, production-stable REST server out of the box without specific flags (-server <port>) or the Inferno fhir-validator-wrapper / HAPI FhirInstanceValidator. Also, disabling terminology (-tx n/a) in validator_cli.jar turns extensible/preferred value-set checks into warnings or skips, which must be parsed explicitly from the returned OperationOutcome (issue.severity and issue.details) so Tier 0 NOT_CHECKED lanes aren't silently misclassified as PASS.

#### B. Licensing & Supply Chain Traps

- **ICD-10-CM + RxNorm + LOINC Redistributability in Docker:** While NLM's RxNorm Current Prescribable Content requires no UMLS license and CMS's ICD-10-CM tabular files are public domain in the US, LOINC explicitly prohibits redistributing the full LOINC table inside public container images without users accepting the Regenstrief license, and HL7 US Core 6.1.0 NPM packages (hl7.fhir.us.core#6.1.0.tgz) pull transitive terminology dependencies (hl7.terminology.r4). Pre-seeding .fhir package caches inside a published Docker image vs. populating a volume via a first-run setup script (auris init --accept-licenses) is a critical distinction. Put all model downloads, RxNorm fetches, and .fhir package cache warming behind auris setup, and mount them into Docker via a host volume rather than baking third-party weights/terminology into a pushed Docker Hub/GHCR image.

- **SpeechBrain Pretrained ECAPA-TDNN Provenance:** speechbrain/spkrec-ecapa-voxceleb is Apache-2.0 on Hugging Face, but it is trained on VoxCeleb1/2, which has notorious academic-only / non-commercial terms and biometrically scraped YouTube data. Since your plan explicitly states "unknown training provenance is recorded, not laundered by a permissive weight licence", you must explicitly record VoxCeleb's non-commercial/academic upstream dataset provenance in the artifact manifest so enterprise deployers aren't blindsided.

#### C. Evaluation Flaws

- **ACI-Bench & PriMock57 Vital/Order Sparsity:** Have you checked the class distribution of the 4 target resources in PriMock57? PriMock57 consists of telemedicine/mock consultations (mostly history-taking over audio/video). Physical vital sign measurements (Observation with numeric value + unit) are exceedingly rare in PriMock57 because the doctor cannot physically take a blood pressure or temperature over a mock phone call! If your 30-encounter gold FHIR subset or 60-encounter reduced test set draws heavily from PriMock57, your statistical power on Observation (vital-signs) will be near zero (wide CIs spanning 0.30–0.95).

- **Span-Free Fact Matching for Clinical Equivalence:** How does the evaluation harness score an extracted Condition ("High blood pressure" linked to turn 14) against a gold Condition ("Essential hypertension" linked to turn 42)? You need an explicit bipartite matching specification (Hungarian algorithm over (resource_type, experiencer, polarity, clinical_concept_cluster) before computing token/code exactness), otherwise two valid mentions of the same chronic condition at different timestamps in the encounter will score as 1 False Positive + 1 False Negative.

#### D. Competitive Positioning (Infherno / phlox / AWS HealthScribe / Epic–Abridge)

- **Why Infherno actually failed on local models:** Infherno (j-frei/Infherno) didn't just lack constrained decoding—it used SmolAgents (multi-step code/tool-calling agent loops) where small local LLMs had to autonomously query a Snowstorm SNOMED server, inspect results, and construct deeply nested FHIR R4 JSON in one go. Multi-turn agentic tool-use + deep FHIR nesting compounds per-step error rates exponentially on 8B models. Auris beats Infherno not merely by "constrained decoding", but by eliminating agentic loops and decoupling extraction (flat reduced schema) from terminology resolution (deterministic retrieval + bounded multiple-choice). State this architectural contrast explicitly.

- **AWS HealthScribe & Epic/Abridge Class Parity:** Commercial leaders (AWS HealthScribe, Abridge) win clinical trust through clickable transcript provenance (every generated clinical insight links to segmentId + beginAudioOffset/endAudioOffset) and section-level attribution. However, AWS HealthScribe outputs a proprietary JSON clinical insights schema—not validated US Core FHIR R4 resources—and requires cloud data egress. Positioning Auris as "The open-source, zero-egress equivalent to AWS HealthScribe's evidence-linked architecture, outputting native US Core FHIR R4 instead of proprietary JSON" gives immediate mental clarity to health-tech engineers and enterprise evaluators.

### 3. Concrete Amendments Only (Apply to Lock docs/PLAN.md)

Apply these exact modifications to docs/PLAN.md to turn my BLOCK into a LOCK:

#### Amendment 1 — Fix §3 Export Matrix (Vital Timestamps, BP Components, Negation/Refutation, and Anchor Cardinalities)

Replace the opening paragraph and table of ## §3 Export and acceptance matrix with:

```markdown
## §3 Export and acceptance matrix

`meta.profile` is always set by the mapper; a resource without it claims base R4 only.
Intake context MUST supply `Patient`, `Encounter` (including `Encounter.period.start` as an ISO-8601 `YYYY-MM-DD` or full `dateTime`), and a default `Practitioner` anchor; none are ever extracted or invented.
Validation lanes report `PASS` / `FAIL` / `NOT_CHECKED` separately (reduced schema → evidence and consistency → base R4 → declared US Core profiles, pinned `hl7.fhir.us.core#6.1.0` → terminology).
Tier 0 terminology (local tables, validator `-tx n/a`) always sets the terminology lane to `NOT_CHECKED` and can never produce a "fully validated" label; unchanged reviewed state is invalidated by any edit; validator feedback may only trigger evidence-grounded structural repair (max one loop), never invented clinical facts.

| Assertion | Polarity | Resource / US Core 6.1.0 Profile | Fixed Semantics & Mapper Rules | Export Rule |
|---|---|---|---|---|
| Vital sign (numeric value + unit) | Affirmative | `Observation` (specific US Core vital profile, e.g., `us-core-heart-rate`, `us-core-blood-pressure`) | `status=preliminary`, UCUM unit. Blood pressure maps to panel `85354-9` with systolic (`8480-6`) and diastolic (`8462-4`) in `component` slices (no root `value[x]`). If no explicit time is spoken, `effectiveDateTime` deterministically inherits `Encounter.period.start` (`vs-1`) and records a provenance note. | Export if lanes pass; else review. |
| Symptom statement | Affirmative or Negated | No FHIR resource in v1 | Ledger entry (`polarity=affirmed\|negated`), rendered under SOAP `S` as transcript-supported text (pertinent positives/negatives). | Review-visible; never exported as a FHIR resource in v1. |
| Clinician-assessed problem | Affirmative | `Condition` (`us-core-condition-encounter-diagnosis`) | `clinicalStatus=active`, `verificationStatus=provisional`, `category=encounter-diagnosis`. | Export only if speaker role is `clinician` and lanes pass. |
| Clinician-ruled-out problem | Negated (`ruled_out`) | `Condition` (`us-core-condition-encounter-diagnosis`) | `verificationStatus=refuted` (`clinicalStatus` omitted per `con-3` invariant). | Routed to review queue by default; exported only after clinician confirmation in review. |
| Patient-reported history/condition | Affirmative | `Condition` (`us-core-condition-problems-health-concerns`) | `clinicalStatus=active`, `verificationStatus=unconfirmed`, `category=problem-list-item`. | Experiencer must be `patient`; negated history stays in ledger (`S`) rather than exporting an unconfirmed/refuted Condition. |
| Explicit new spoken order | Affirmative | `MedicationRequest` (`us-core-medicationrequest`) | `status=draft`, `intent=proposal`, `requester` bound to intake `Practitioner` anchor; dose instructions verbatim in `dosageInstruction.text`. | Ambiguous discussion or negated orders never become a request; Auris never activates/signs. |
| Reported / continuing medication | Any | No `MedicationRequest` in v1 | Ledger + review queue (`MedicationStatement` is post-v1 scope). | Never exported as an order. |
| Explicit allergy / intolerance | Affirmative | `AllergyIntolerance` (`us-core-allergyintolerance`) | `clinicalStatus=active`, `verificationStatus=unconfirmed`. Drug allergens mapped via RxNorm; non-drug allergens without SNOMED emit text-only `code.text` and set the terminology/US Core extensible binding lane to `NEEDS_CODING_REVIEW`. | Explicit statement only; silence ≠ "no known allergies". |
| Explicit allergy refutation / NKA | Negated | No `AllergyIntolerance` exported without SNOMED (`716186003`) | Recorded in ledger + review queue; if SNOMED CT is absent, rendered in SOAP `S` as `"Denies [allergen] / NKA (transcript-supported)"` rather than exporting an uncoded or inverted resource. | Never exported with `verificationStatus=unconfirmed`; blocked from FHIR Bundle unless confirmed with valid refutation code. |
```

And amend the "Material assertion" paragraph immediately below the table to read:

```markdown
"Material assertion" = any medication, allergy, negated/refuted assessment, or non-patient-experiencer assertion. Material + **unmapped or validation-failing (unresolved)** ⇒ final FHIR Bundle export blocked until clinician review resolves or dismisses the item (affirmed pertinent negatives routed cleanly to the SOAP ledger do not block export). Integration proof builds a self-contained `transaction` Bundle using `urn:uuid:` `fullUrl` references for intake `Patient`, `Practitioner`, and `Encounter` alongside the extracted clinical resources, verified via `POST /` against a local HAPI FHIR R4 test server.
```

#### Amendment 2 — Fix Quote Verification, Backend Equivalence, and Cache Redistributability in ## Approach & ## Steps

1. **In ## Approach → Extraction:** Replace "mandatory verbatim quote (mechanically checked against the transcript; result labelled 'transcript-supported', never 'verified')" with:

   > "mandatory transcript evidence span (segment_id + verbatim quote, mechanically verified against the immutable transcript via case- and punctuation-normalized token-span alignment that resolves exact character/audio offsets; unaligned quotes fail the evidence lane; aligned results are labelled transcript-supported, never verified)."

2. **In ## Approach → Extraction:** Replace "Backend contract tests must prove identical schema enforcement before either is trusted" with:

   > "Backend contract tests must prove AST-level schema conformance and identical rejection of invalid structures across llama.cpp (GBNF) and vLLM+XGrammar (accepting that floating-point hardware differences mean token streams are not bit-identical)."

3. **In ## Steps → Slice 3 & Licensing throughout:** Add:

   > "Published Docker images contain code and runtime dependencies only; model weights, RxNorm tables, and HL7 .fhir package caches are fetched via auris setup into a mounted cache volume (with --export-cache / --import-cache tarballs for air-gapped deployment) so Auris never redistributes third-party terminology or biometric dataset derivatives (e.g., VoxCeleb-trained SpeechBrain weights, flagged in the artifact manifest as upstream academic-dataset provenance)."

#### Amendment 3 — Fix Vital-Sign Sparsity and Bipartite Matching in ## Validation Plan

1. **In ## Validation Plan → Splits and leakage discipline:** Append:

   > "Because mock/telemedicine corpora (especially PriMock57) contain sparse spoken physical vital signs, the commissioned audio fixtures and evaluation reporting must explicitly track per-class denominator counts (N assertions per resource type); any class with N < 25 in the test split must be flagged as low-power and supplemented by a targeted clinical-vitals/orders stress suite before headline class-level F1 claims are made."

2. **In ## Validation Plan → Metrics:** Append after "assertion P/R/F1 per class at declared coverage":

   > "computed via optimal bipartite matching (Hungarian algorithm) over (resource_class, experiencer, polarity, clinical_concept) with a secondary strict match requiring valid evidence-span overlap and code equivalence."

#### Amendment 4 — Sharpen ## Positioning

Add one sentence to ## Positioning:

> "Architecturally, Auris avoids the multi-step agentic tool loops that caused local models to fail in Infherno (replacing agentic FHIR generation with single-pass constrained reduced-schema extraction + deterministic mapping), and serves as an open-source, zero-egress counterpart to AWS HealthScribe—combining audio-offset transcript provenance with native US Core R4 validation instead of proprietary JSON."
