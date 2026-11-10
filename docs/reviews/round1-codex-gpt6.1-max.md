# Auris — proposed plan for Muse’s merge

**Recommendation:** build a conservative, evidence-linked audio-to-FHIR pipeline with a portable baseline, replaceable components, and an evaluation harness shipped alongside the product. Its advantage should be inspectable clinical assertions and reproducible measurements.

This is a planning draft. Muse should verify repository paths, model-card licences, release pins, and artifact hashes before locking `docs/PLAN.md`. No application code before Chandra’s “go.”

## (a) Stage recommendations

### Define the output contract first

Each encounter produces:

- A FHIR R4 `Bundle` of type `collection`, containing the four supported clinical resource types and necessary patient/encounter references.
- A deterministic SOAP **summary** rendered from accepted FHIR resources.
- A separate evidence manifest linking assertions to transcript spans, audio timestamps, speaker attribution, and mapping decisions.
- Machine-readable validation results and a review queue for unresolved assertions.

Treat transcript text as untrusted input. It cannot issue pipeline instructions, call tools, or change schemas.

Distinguish these states throughout the API and UI:

1. Extracted candidate.
2. Structurally valid candidate.
3. Passed the declared FHIR/profile/terminology checks.
4. Clinician reviewed.

None implies the next.

### Recommended components

| Stage | Recommendation | Strongest alternative and decision |
|---|---|---|
| Audio ingestion | Initially accept PCM WAV; preserve original channels and timestamps. Use Python `wave` and NumPy. Preserve the original recording separately from the normalized ASR input. | FFmpeg expands supported formats, but introduces binary-build and redistribution obligations. Add an explicitly packaged conversion option after WAV works. |
| ASR | `faster-whisper` and CTranslate2, both MIT. Portable demo: `Systran/faster-whisper-small.en`, CPU INT8. Linux quality baseline: `Systran/faster-whisper-large-v3`, using weights derived from MIT-licensed Whisper. English only for the initial supported product. | WhisperX, BSD-2-Clause, adds forced alignment and orchestration. Add it only if measured evidence-location errors justify the extra models and dependencies. `distil-whisper/distil-large-v3`, MIT, is a speed candidate requiring its own medical-audio evaluation. |
| Alignment | Begin with faster-whisper word timestamps. Label them approximate evidence locators; retain original audio for replay. | WhisperX with a separately licensed, pinned alignment model. More precise timestamps do not establish correct words or clinical meaning. |
| Diarisation | Portable, ungated baseline: Silero VAD, MIT; `speechbrain/spkrec-ecapa-voxceleb`, Apache-2.0 model card; SpeechBrain, Apache-2.0; agglomerative clustering through scikit-learn, BSD-3-Clause. Calibrate clustering on development audio; never force every encounter into two speakers. | Evaluate `nvidia/diar_sortformer_4spk-v1` through NeMo, Apache-2.0 code, with the specific weight licence checked and recorded; its published licence is CC BY 4.0. Four-speaker capacity is a limitation. `pyannote/speaker-diarization-3.1` is another serious comparator, but its gated access makes it an optional installation, not the default demo dependency. |
| Role assignment | A separate component returning `clinician`, `patient`, `other`, or `unknown`. Use explicit introductions/session setup when available; use lexical features and the local extractor model to propose assignments. Require review when attribution remains ambiguous. | A dedicated supervised role classifier becomes preferable once Auris has appropriate labelled data. An LLM’s self-reported confidence is not a calibrated probability. |
| Extraction | `Qwen/Qwen3-8B`, Apache-2.0. Laptop reference: Q4_K_M through `llama.cpp`/`llama-cpp-python`, both MIT. Use an official or reproducibly converted GGUF artifact with a recorded upstream revision. Extract into small Pydantic 2 schemas, MIT, with schema-constrained decoding. | Linux throughput backend: vLLM, Apache-2.0, and XGrammar, Apache-2.0. Evaluate `Qwen/Qwen3-32B`, Apache-2.0, as a larger-model comparator. Do not assume size or medical branding establishes better extraction. |
| Terminology and mapping | Deterministic Python mappers. Retrieve candidates from versioned local terminology tables using SQLite and RapidFuzz, MIT. The model identifies clinical concepts; it does not invent terminology codes. | A larger terminology service supports broader coverage, but does not remove ambiguity. Use HAPI FHIR JPA, Apache-2.0, as the local terminology backend when required by the supported profiles. |
| Validation | HL7 FHIR Validator CLI, Apache-2.0, pinned JAR and checksum; R4 `4.0.1`; initial US Core package `hl7.fhir.us.core#6.1.0`. Add local clinical consistency checks and reference-resolution checks. | `fhir.resources`, BSD-3-Clause, can provide convenient Python models after its exact R4 support is verified. Do not let an R4B/R5 default silently substitute for R4. It is not the profile-validation authority. |
| Note rendering | Fixed Python/Jinja2 templates; Jinja2 is BSD-3-Clause. Render only accepted resources, their explicit statuses, and supported narrative fields. Escape output. | An LLM renderer introduces another opportunity for unsupported content. Exclude it from the initial core path. |

The classical diarisation baseline is a deliberate portability choice, not a claim of competitive accuracy. Publish its weaknesses and replace it if the measured alternatives justify their operational cost.

Keep model environments separate where dependencies conflict. The portable installation must not require vLLM, CUDA, NeMo, or gated-model credentials.

### Reduced extraction schema

Each atomic assertion should contain:

- Clinical concept and proposed resource category.
- Source utterance IDs and exact supporting transcript text.
- Subject: patient, relative, or another person.
- Present/past/future context and any explicitly stated date.
- Negation and uncertainty.
- Reporter and speaker attribution.
- For medications: discussion, historical use, continuation, explicit prescription, or unresolved intent.
- Explicitly stated values, units, dose components, route, frequency, and duration.

Missing information remains missing. Do not complete a prescription from “usual dosing.”

Validate evidence references mechanically, then apply semantic checks. A matching quotation establishes traceability to the transcript; it does not establish that ASR heard it correctly.

Use overlapping context windows with stable assertion IDs. Deduplication must preserve corrections and changes over time.

### FHIR mapping rules that must be explicit

- Patient-mentioned diagnoses do not automatically become clinician-confirmed Conditions.
- Family history does not become the patient’s Condition.
- Mentioning a medication does not create a MedicationRequest.
- An explicit clinician prescription can produce `MedicationRequest.status=draft` and appropriate intent; Auris does not sign or activate it.
- Historical medication use needs a different resource, such as MedicationStatement. Keep it in the review queue while that type remains outside scope.
- Missing allergy information, “not asked,” and an explicit “no known allergies” statement are different states.
- Do not infer “allergy” versus “intolerance” when the distinction was not established.
- Preserve uncertainty and source attribution rather than choosing whatever status makes validation easiest.

Use a documented profile support matrix. Start with specific vital-sign profiles, the appropriate US Core Condition profiles, MedicationRequest, and AllergyIntolerance. A generic symptom Observation may initially have only a base-R4 validation claim.

### Validation behavior

Run these checks separately and retain their results:

1. Reduced-schema checks.
2. Evidence and clinical consistency checks.
3. FHIR R4 structure, references, and invariants.
4. Declared US Core profiles.
5. Required terminology bindings.

Terminology validation must use a local endpoint or a demonstrably complete local cache. Disabling terminology checks cannot produce a “fully validated” badge. Unavailable terminology produces an incomplete result.

Do not invent demographics, encounter facts, codes, units, or dates to satisfy a profile. Patient and encounter anchors come from supplied intake context; the demo uses fictional anchors.

MustSupport also creates implementation obligations beyond validating an individual instance. Document and test the relevant support behavior separately.

An encounter with a material unresolved assertion can produce a clearly marked preview. Final export remains blocked until required review and validation succeed.

### Resolve the SOAP scope honestly

Four clinical resource types cannot represent an unrestricted SOAP note.

For v1:

- **S:** supported patient-reported symptom observations and allergy statements.
- **O:** supported measurements, with their actual source and method.
- **A:** Conditions with their recorded certainty and status.
- **P:** explicit medication orders represented as draft MedicationRequests.

Do not infer that an observation is objective merely because the clinician said it. Do not hide the whole transcript inside `resource.note`.

Counselling, follow-up plans, referrals, procedures, and historical medications will exceed this scope. Surface those omissions during review. If a complete clinical note is mandatory, expand the resource contract deliberately—likely with CarePlan, ServiceRequest, and MedicationStatement—before claiming that capability.

## (b) Licensing, redistribution, and offline deployment

Use **Apache-2.0 for Auris code**. Maintain separate notices and permissions for dependencies, model weights, terminology, and datasets.

Every downloadable artifact needs a manifest containing its source, immutable revision, checksum, licence, attribution requirements, access conditions, and redistribution decision.

Specific traps:

- **WhisperX is an integration, not a single licensing decision.** Inspect its alignment and diarisation artifacts independently.
- **Pyannote access is gated.** A permissive stated licence does not remove acceptance requirements. Do not mirror weights or embed credentials to bypass them.
- **NeMo licences do not establish weight licences.** Check the exact Sortformer checkpoint. Do not extend the `diar_sortformer_4spk-v1` licence conclusion to streaming/v2 models.
- **Qwen is a family, not one licence.** Approve the selected Qwen3-8B and Qwen3-32B artifacts individually, including quantized derivatives.
- **Speaker-encoder licences do not license training recordings.** Do not redistribute VoxCeleb recordings merely because the encoder has an Apache-2.0 model card.
- **LOINC, UCUM, RxNorm, and SNOMED CT are not one permissively licensed dictionary.** Record each distribution’s terms. SNOMED territorial/affiliate conditions make a universally bundled terminology image problematic. Restrict RxNorm imports to approved content rather than silently redistributing all source vocabularies.
- **ACI-Bench and PriMock57 need dataset-specific review.** Capture original data licences, recording terms, and derivative-annotation permissions. A repository’s code licence is insufficient. Initially provide download manifests rather than vendoring either corpus.
- **FFmpeg obligations depend on its build.** Prefer an approved LGPL build if introduced.
- A bundled **Eclipse Temurin 21** runtime carries GPL-2.0 with the Classpath Exception and its required notices.

The public demo should use a commissioned, human-recorded fictional consultation with explicit recording and redistribution permission.

Offline operation must be testable:

- Download approved artifacts before ingesting an encounter.
- Run successfully with external networking disabled.
- Resolve terminology locally.
- Disable telemetry and PHI-bearing logs.
- Document every external bootstrap request.
- Support importing an artifact cache into an air-gapped machine.
- Make retention explicit; model caches and encounter recordings have different lifecycles.

Do not describe all weights as “Apache-licensed” or equate open weights with fully open training data.

## (c) Three demoable MVP slices

### Slice 1 — prove the complete contract on one controlled encounter

**Demonstration:** fictional audio → actual ASR → actual local extraction → mapped FHIR → actual validator → resource-derived SOAP summary.

Include one supported example of each clinical resource type.

Stub:

- Diarisation with a supplied, clearly labelled oracle speaker timeline.
- Role assignment with supplied roles.
- Terminology retrieval with a small approved vocabulary.
- Patient/encounter context with fictional intake data.

Do **not** stub the extractor, mapper, validator, or renderer.

Deliver:

- Evidence links for every assertion.
- An intentionally invalid example showing export refusal.
- A documented profile support matrix.
- A multiarchitecture CPU demo image.

Target one-command try-out after Docker installation:

```sh
docker run --rm -v auris-cache:/cache -v "$PWD/out:/out" \
  ghcr.io/supermax01/auris:0.1.0 demo --download-models
```

This is a proposed release interface, not an existing command. Publish first-run download size and measured runtime. Do not promise real-time execution on a laptop.

### Slice 2 — remove oracle speakers and add review

**Demonstration:** previously unseen human-recorded mock consultation → diarisation → role proposals → local review → all four resource types → validation → SOAP summary.

Add:

- Portable diarisation backend and measured comparator.
- Unknown-role and overlap handling.
- Negation, uncertainty, family attribution, corrections, and medication-intent handling.
- Local evidence playback and correction of speaker roles and clinical assertions.
- Revalidation and rerendering after edits.

Keep EHR integration stubbed. Export a collection Bundle; do not submit prescription or chart-write transactions.

Exit criterion: the demo visibly handles uncertainty and refuses a plausible but unsupported prescription.

### Slice 3 — reproducible research and offline deployment

**Demonstration:** run a locked evaluation configuration, then process a new encounter with networking disabled.

Deliver:

- Linux CPU and optional GPU deployment paths.
- Native Apple Silicon development through mise/uv; optional Metal inference.
- Backend interfaces and contract fixtures for ASR, diarisation, role assignment, extraction, and validation.
- Reproducible evaluation commands, dataset cards, annotation protocol, and model manifests.
- Published accuracy, coverage, latency, peak memory, and hardware measurements.
- An auditable offline test, cache-import instructions, and documented retention behavior.

A real-clinic evaluation depends on obtaining authorized recordings. Without them, release a research preview and describe the evidence as mock-consultation evidence.

## (d) Honest evaluation plan

### Establish gold independently

Begin with 20 development encounters and target 60 locked test encounters. These are proposed collection sizes, not available datasets or statistical sufficiency claims.

Separate participants, clinicians, and sessions across splits. Include recording conditions and speaking styles that challenge the system. Publish the selection procedure and class counts.

For every gold encounter:

1. Two clinical annotators trained in the FHIR protocol independently inspect the audio and corrected transcript, without seeing Auris output.
2. They identify atomic clinical assertions, supporting timestamps, subject, attribution, negation, uncertainty, temporality, and order intent.
3. They independently encode the supported assertions into FHIR using the same authorized intake context available to Auris.
4. A clinical adjudicator and FHIR specialist resolve disagreements.
5. Preserve pre-adjudication annotations and an adjudication log.

Record both **in-scope gold assertions** and **clinically important unsupported assertions**. Otherwise, the four-resource boundary can artificially improve precision while concealing lost information.

Evaluate semantic tuples rather than byte-identical JSON. Predeclare acceptable code equivalences, unit conversions, and numeric tolerances. Never add equivalences after inspecting model mistakes to improve the score.

### Report inter-annotator agreement

Before adjudication, report:

- Assertion detection and evidence-span agreement.
- Concept/code agreement.
- Value and unit agreement.
- Medication component and order-intent agreement.
- Speaker-role, subject, negation, uncertainty, and temporality agreement.
- Agreement by resource class.

Use pairwise precision/recall/F1 for sets and relations; use Cohen’s kappa for suitable categorical labels, alongside raw agreement and class prevalence. One aggregate kappa would conceal the important disagreements.

### Use ACI-Bench and PriMock57 correctly

- **ACI-Bench:** its 207 encounters have note targets, not gold FHIR. Use permitted source material for auxiliary note evaluation or independently annotate a clearly identified subset for FHIR.
- **PriMock57:** use its human-recorded mock consultations to evaluate transcription and downstream extraction after independent annotation. It is not prospective real-clinic evidence.
- Label any new FHIR annotations as Auris-derived annotations, with their protocol and permitted distribution terms.
- Never treat the benchmark reference note as an exhaustive record of everything said.
- Same-family generated dialogues and TTS audio are regression fixtures, not headline clinical-quality evidence.

### Measure each stage and the complete pipeline

Publish:

- ASR WER plus medical-concept, medication-name, number, and dose errors.
- Diarisation DER/JER with overlap policy and collar settings disclosed.
- Role accuracy, confusion, abstention, and coverage.
- Per-class clinical assertion precision/recall/F1.
- Exact medication-component accuracy and unsupported-order rate.
- Observation value/unit errors.
- Negation, temporality, subject, and certainty errors.
- Base-R4, profile, terminology, and reference-check outcomes separately.
- Clinician correction burden and unsupported facts in the rendered note.
- Runtime, peak memory, hardware, quantization, and artifact revisions.

Compare actual audio against gold-transcript extraction; compare actual speakers/roles against oracle attribution. This identifies whether extraction failures originate upstream.

Use rules/medspaCy and the unadapted local LLM as baselines. Compare larger models on the same locked data and deployment budget.

Report encounter-level confidence intervals and accepted-output coverage. A system that abstains on most encounters must not win through precision alone.

**F1 ≥ 0.80 remains a target, not a result.** Validation success is not extraction accuracy. Zero observed serious errors in a small test is not proof of safety; zero events in 60 independent encounters still permits an upper 95% error-rate bound of roughly 5%.

## (e) Five likely real-clinic failures

| Failure | Mitigation |
|---|---|
| **Noise, reverberation, accents, and medication/number confusions** | Preserve original audio; expose evidence playback; measure critical-token errors separately from WER. Flag uncertain dose/value extraction and require review. Never silently replace an unfamiliar drug with a plausible one. |
| **Overlap, clinician interruptions, relatives, interpreters, and speaker-label swaps** | Preserve channel information, detect likely overlap, allow additional/unknown speakers, and review ambiguous role attribution. Do not use a two-speaker assumption to authorize an order. Document backend speaker limits. |
| **Negation, uncertainty, family history, and changing temporal context** | Annotate these explicitly before FHIR mapping. Retain corrections and historical statements separately. Test contrasts such as “mother has diabetes,” “rule out diabetes,” and “no diabetes.” |
| **Medication discussion becomes a prescription; terminology or units change meaning** | Require explicit clinician order intent. Preserve incomplete instructions; never infer standard doses. Validate terminology, UCUM units, component relationships, and source-supported values. Keep historical medications outside MedicationRequest. |
| **Long encounters, recording gaps, truncation, and earlier facts being overwritten** | Track recording continuity and processing coverage; use overlapping windows, stable assertions, and explicit correction handling. Never label an incomplete recording complete. Block finalization when material gaps or unresolved contradictions remain. |

## Competitive analysis for Muse to verify

These are knowledge-based starting points. No current repository audit or competitive performance claim is implied. Entries marked **UNVERIFIED** need repository-path and capability confirmation before entering the final plan.

| Project | Accomplishment and limit | Concrete Auris differentiation |
|---|---|---|
| **HealthSage-AI/healthsage-ai-llm — UNVERIFIED exact path/current scope** | Reported open-weight work on converting clinical text into FHIR. Verify released models, supported resource types, licences, validation procedure, and evaluation provenance. Do not infer audio capability or clinical accuracy from generated FHIR examples. | Publish independently annotated audio-to-FHIR evaluation, source evidence, explicit validation failures, and a fully offline runnable pipeline. Beat its released baseline on the same supported assertions before claiming better extraction. |
| **aws-samples/amazon-healthscribe-demo — UNVERIFIED exact path** | Amazon HealthScribe demonstrates managed transcription and clinical-note generation with source references; confirm what this sample actually implements. The managed service does not satisfy Auris’s self-hosted open-weight core requirement. | Run disconnected after bootstrap and export validated structured resources with reproducible local models. Compare evidence usability and supported clinical facts; do not claim superior note quality without testing. |
| **medplum/medplum** | An established open-source FHIR platform and application ecosystem, Apache-2.0. Platform functionality alone is not evidence of accurate ambient extraction. Verify current scribing examples separately. | Supply the auditable upstream audio-to-FHIR pipeline and evaluation harness. Integrate through FHIR rather than rebuilding Medplum’s platform. |
| **openemr/openemr** | A mature clinical EHR with documentation workflows and FHIR interoperability, GPL-3.0. That establishes deployment experience, not an evaluated open-weight audio-to-FHIR extractor. | Produce a reviewable, evidence-linked import candidate and demonstrate interoperability. Avoid copying GPL code into the Apache core without a deliberate licensing decision. |
| **medspacy/medspacy** | MIT-licensed clinical NLP tooling for concepts, sections, and context such as negation. It provides useful text-processing primitives rather than a complete ambient pipeline. | Include a medspaCy/rule baseline and show whether the LLM improves supported clinical recall without increasing harmful unsupported assertions. |
| **m-bain/whisperX** | Audio transcription, alignment, and speaker-diarisation integration. It does not itself establish clinical role assignment, clinical extraction accuracy, or FHIR conformance. | Add independently evaluated role attribution, structured clinical semantics, terminology checks, validation, and deterministic resource-derived documentation. |

For each audited competitor, Muse should record the commit SHA, licence, executable example, released artifacts, supported outputs, evaluation data provenance, and reproduced result—or explicitly record that a result was not reproduced. README claims and stars are not evidence of accomplishment.

## Top three disagreements I expect

1. **Diarisation default:** teammates may prefer pyannote or Sortformer immediately. I favor an ungated portable baseline, with a measured upgrade path and separately reported quality.
2. **SOAP scope:** teammates may promise a complete note from four resource types. I would constrain the claim or expand the resource contract before implementation.
3. **Output acceptance:** teammates may favor exporting plausible partial results. I would expose previews while blocking final export for material uncertainty, unresolved terminology, or required validation failures.