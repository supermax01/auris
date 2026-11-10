**BLOCK — the profile-specific export contract is still undefined.** The default path combines partial terminology checking, text-only coding fallbacks, and unspecified US Core profiles, yet promises a validated Bundle. We need an explicit acceptance boundary before implementation.

**Q1 — Ratify two profiles, with a provisional portable default.**

The interface and ungated demo path are sound choices. Calling pyannote the “quality profile” is premature; call it the **optional comparison profile** until measured.

Concrete amendments:

- Specify the portable algorithm beyond “ECAPA + clustering”: embedding windows, minimum speech duration, speaker-count estimation, clustering thresholds, short-turn assignment, and overlap handling. Silero supplies speech detection, not speaker segmentation.
- Preserve `unknown` and overlapping speech instead of forcing assignments. Test clinically important short responses such as “no,” which VAD and embedding windows can lose.
- Treat channel attribution as speaker evidence only when the recording setup establishes channel identity. Stereo does not inherently mean one person per channel, and channel identity does not establish clinical role.
- Let reviewers correct roles at segment level. One erroneous speaker merge must not force every affected turn into the same role.
- Recorded CI fixtures verify downstream contracts; they do not establish that the actual diariser works. Include a small runnable diarisation check outside routine lightweight CI.

Choose between the profiles using the same frozen development recordings, including interruptions, companions, short turns, and overlap. No evidence supplied here establishes either profile’s superiority.

**Q2 — Accept 8B as the initial laptop candidate, not a proven deployment default.**

Freeze the exact checkpoint, quantisation artifact, chat template, thinking/non-thinking mode, context limit, decoding settings, and reference Mac memory budget. “8B Q4_K_M” alone is not reproducible. Also resolve `small.en/distil-large-v3`: those are two materially different ASR profiles, not one portable specification.

The default should flip when:

- The 8B candidate fails a predeclared clinical acceptance criterion and a larger candidate passes within the declared hardware/runtime budget; or
- A larger candidate provides a predeclared meaningful improvement in supported-fact recall or correction burden without worsening critical errors.

Compare at declared coverage: a model that abstains on every medication must not win on unsupported-order rate alone. If every candidate fails the clinical acceptance criteria, none becomes the quality default.

Select on development/model-selection data, then freeze before the final test. “Decide on locked eval” currently risks turning the test set into a model-selection set.

One implementation claim also needs qualification: one OpenAI-compatible client does not establish equivalent schema enforcement across llama.cpp and vLLM. Require backend contract checks for the exact schema subset, including nullable fields and rejection of unsupported schema features.

**Q3 — Accept fact gold plus an independent FHIR subset; reject 150 hours as an established budget.**

The synthesis is better than using the production mapper to generate all FHIR gold. Independence must mean **independent of that mapper’s output**. Preserve alternative valid encodings and adjudicate semantic equivalence; do not force every annotator into the mapper’s preferred representation.

Thirty encounters is a sampling frame, not a coverage guarantee. Set minimum counts for each resource type and important edge case, especially actual prescribing intent, negation, experiencer, and uncertain allergies.

The budget arithmetic is already tight:

- 117 encounters × two annotators = 234 primary annotation passes.
- 150 hours allows approximately **38 minutes per pass**, before separate FHIR annotation, adjudication, guideline development, transcript correction, or QA.

Replace the estimate with a timed 5–10 encounter pilot and a budget formula covering those activities. Clarify whether the 30 private encounters are additional or included.

There are three evaluation defects to fix:

1. **PriMock57 leakage:** Slice 2 develops the system on PriMock57, while evaluation calls all 57 test encounters. Allocate development recordings explicitly and exclude them from held-out claims, or keep PriMock57 untouched during development.
2. **Audio availability:** verify which ACI-Bench artifacts actually provide usable public audio and timing information. A transcript-only item cannot support ASR, diarisation, or audio-grounded annotation metrics. TTS would remain a separate simulated condition.
3. **Private-set meaning:** withholding annotations for public dialogues does not make those dialogues unseen. Define the private set’s source and split unit; a canary cannot establish absence of training contamination.

Two wording amendments: call IAA **observed human agreement**, not a “human ceiling”; and report out-of-scope content against encounter coverage, rather than suggesting its recording alone prevents inflated precision.

**Q4 — Corrections and coordinator verification queue.**

I have not independently reverified current artifacts in this round. Repository observations supplied by the coordinator can stand where they have dated evidence; they do not establish inherited model rights or capabilities absent from a README.

| Claim | Required correction or verification |
|---|---|
| **faster-whisper/CTranslate2 MIT; Whisper and distil artifacts** | Keep software and weight licences separate. Pin upstream weights, converted artifact, conversion settings, revisions, and applicable notices. Confirm the selected faster-whisper version supports the proposed options; hallucination-silence handling depends on word-timestamp settings. ASR scores are not calibrated clinical-fact probabilities. |
| **Silero / SpeechBrain portable baseline** | Verify exact VAD and ECAPA checkpoint licences and access conditions, separately from toolkit licences. “Ungated” is not equivalent to unrestricted redistribution or documented training provenance. |
| **pyannote 3.1** | Pin the full pipeline and its dependent checkpoints, terms, and revisions. Distinguish gated acquisition from authenticated runtime use; demonstrate operation from an imported offline cache. |
| **Sortformer NC versus CC-BY** | Neither family-level claim resolves the dispute. Require the exact checkpoint ID and model-card revision; different NVIDIA checkpoints carry different terms. Check speaker-count and streaming constraints too. “Commercial-clean” is not a licence category. |
| **Qwen3 Apache-2.0 family, including GGUF** | Verify each chosen checkpoint and quantisation artifact. A community GGUF needs traceable upstream provenance and applicable notices. Pin llama.cpp, vLLM, and XGrammar versions and supported decoding interfaces. The broad family label cannot substitute for artifact review. |
| **R4 4.0.1 / US Core 6.1.0 / validator** | These are valid historical targets; age alone is not an error. Choose 6.1.0 for declared compatibility, rather than implying it is current. Pin the validator, core/IG packages, transitive packages, and terminology dependencies. `fhir.resources` needs a demonstrated R4 4.0.1 release/import path and compatible Pydantic version; a current top-level install is insufficient. |
| **HAPI/Snowstorm terminology tier** | They are not interchangeable complete terminology services. Specify exact versions, supported operations, imported code systems/value sets, and coverage. Verify server and bundled dependency licences. Installing either server does not establish that terminology validation is complete. |
| **LOINC / ICD-10-CM / RxNorm / SNOMED** | Identify exact releases, effective dates, download rights, attribution, and redistribution conditions. “LOINC notice shipped” and “approved RxNorm subset” are underspecified. Define who approves which subset and why its rights permit the proposed use. User-supplied SNOMED does not by itself settle permitted use across deployments. |
| **Dataset and annotation licences** | Check ACI-Bench, PriMock57, MUSAN, the specific RIR corpus, and commissioned recordings separately. Download scripts do not remove restrictions. Do not promise CC-BY-4.0 for stand-off annotations until adaptation rights, evidence quotations, and annotator contribution rights are resolved. |
| **FFmpeg LGPL build only** | Verify the actual binary configuration, linked libraries, version, and distribution obligations. The label alone does not exclude GPL or nonfree components. |
| **“CC-BY weights → NOTICE”; compliance tooling** | NOTICE alone is not the complete attribution procedure. Record the exact licence, attribution, source, and modification information it requires. `pip-licenses` and `reuse lint` do not audit JavaScript, Java, native binaries, model weights, or datasets. |
| **Excluding anything trained on MIMIC/n2c2** | Make this an enforceable provenance policy: documented excluded sources and an explicit disposition for unknown training provenance. Do not imply that a permissive weight licence proves unrestricted source-data provenance. |

Repository and existence claims needing more precise treatment:

- **“Every competitor emits a note” is contradicted by this analysis itself:** Infherno and the purported text-to-FHIR utilities emit FHIR. Replace it with “most inspected ambient-scribe apps are note-first.”
- **“Infherno gave up on local models” overstates the evidence.** Attribute the README’s reported reliability limitation. Constrained decoding is our proposed response, not demonstrated superior reliability. Use **Gemini 2.5 Pro** as the model name.
- **Verify `text2phenotype/fhir-hydrant` both exists at that slug and implements the claimed NLP/text→FHIR conversion.** A FHIR transport or helper library would not support that comparison.
- **The failed HealthSage slug establishes a failed lookup.** It does not exclude a real project or model under another authoritative location.
- For **Infherno and MediNotes**, distinguish “no repository-wide licence grant found at revision X” from GitHub returning no detected licence. Check model artifacts separately. These are public-code prior art; unlicensed code is not thereby OSS.
- Retain the reported repository licences for **scribeHC, ambient-scribe, totalscribe, fhir-hydrant, medspaCy, Medplum, OpenEMR, and WhisperX** only with licence-file permalinks at inspected revisions. Record exact SPDX variants, including “only” versus “or later” where applicable. Root licences do not cover every dependency or model.
- For **aws-samples/aws-healthscribe-demo**, preserve the verified path and distinguish sample software from the managed service it demonstrates. No sample-code licence establishes an open core service.
- Verify Infherno’s **EACL 2026 paper and public demo** through their actual artifacts if those accomplishments enter the final comparison; a README assertion alone is weaker evidence.
- Bound **“no FHIR,” “no evaluation,” “no deploy path,” and “no evidence”** to what was inspected. README absence does not prove implementation absence. Apply this to every competitor row.
- Change “no OSS project does this” and “no competitor ships this” to dated, bounded search findings. Keep the star counts as dated snapshots, not evidence of capability.
- “AGPL deters embedding” is a positioning judgment, not a technical accomplishment or universal legal barrier. Integration with a separate service and copying code have different implications.
- **medspaCy is a toolkit, not a ready-made four-resource baseline.** Specify the actual rules, models, dictionaries, and custom work required.
- Apache-2.0 for Auris remains a **proposed owner decision**. It does not make separately licensed weights, terminology, and deployment dependencies Apache-licensed.
- Replace categorical **“not a medical device”** with intended-use limitations and a deployment-specific regulatory assessment. A disclaimer does not determine classification.

**Q5 — The missing decision is an executable acceptance/export matrix.**

Add one compact table to the final plan: **resource/profile → required supplied context → supported assertion states → permitted coding fallback → validation outcomes → review requirement → export eligibility**.

That table must resolve these concrete defects:

- **Exact profiles:** “Observation” is not one US Core target. Name the supported observation profiles, condition profiles, and other resource profiles. Define what happens when supplied Patient/Encounter context cannot satisfy them. Never manufacture missing clinical information to pass validation.
- **Terminology outcomes:** `code.text` is not a universal escape hatch for required bindings or fixed/pattern codes. Distinguish `PASS`, `FAIL`, and `NOT_CHECKED`; specify whether tier 0 permits final export and exactly what that export claims. Avoid a generic “fully validated” badge.
- **Resource semantics:** `AllergyIntolerance.verificationStatus` does **not** accept `provisional`. Spell out defaults per resource. A `draft` MedicationRequest still represents prescribing intent; it must not be the fallback for an ambiguous medication discussion. Specify `intent`, authorisation evidence, and refusal conditions.
- **Repair authority:** validator feedback must not authorise inventing missing facts or changing clinical meaning. Define permissible evidence-grounded repairs; mapper defects are software defects.
- **Review lifecycle:** define “material assertion,” who can resolve it, and which edits invalidate validation, review, rendering, and export eligibility. An unchanged “clinician-reviewed” label must not survive a changed resource.
- **Bundle integration:** posting a `collection` Bundle as a Bundle resource does not demonstrate ingestion of its entries. Choose either bundle-storage proof or a separate transaction exporter, and test reference resolution.
- **SOAP scope:** four resource types omit substantial encounter content, including historical medication use and counselling. Either narrow the product claim to a structured SOAP summary or approve additional representation. “Not captured by Auris” must distinguish unsupported content that was discussed from information absent from the encounter. Preserve unsupported assertions in the evidence ledger and review coverage.
- **Retention and reproducibility:** reconcile retained originals, evidence playback, and default deletion with an explicit review/expiry lifecycle. Define transcript revisions and offset provenance. `HF_HUB_OFFLINE=1` covers only part of the runtime; preload validator packages and other dependencies for the network-disabled test.
- **Release criteria:** name critical-error classes, refusal conditions, minimum coverage, reference hardware, and who adjudicates failures. “Visibly refuses one unsupported prescription” is a demonstration, not a release criterion.

Without that matrix, extractor size and diariser choice are optimisations around an unresolved product contract.