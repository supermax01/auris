# Auris — plan draft (skeptical staff-engineer review)

Versions and licences below are from memory, not checked this session; re-verify every model-card licence at pin time. The items I am least sure of are marked "verify".

## (a) Per-stage recommendation

| Stage | Decision | Strongest alternative | Why |
|---|---|---|---|
| 1. ASR | `faster-whisper` (MIT, CTranslate2) with `Systran/faster-whisper-large-v3`; Silero VAD on, `word_timestamps=True`, `condition_on_previous_text=False`. Dev/CPU profile: `distil-large-v3` (English-only). | `nvidia/parakeet-tdt-0.6b-v3` via NeMo (CC-BY-4.0): fewer silence hallucinations, good timestamps. | **Drop WhisperX from the core path.** It is a wrapper that pins torch and pyannote, and its wav2vec2 alignment only matters if word-level diarisation error says so. CTranslate2 has no Metal support, so the Mac runs CPU int8; reported numbers come only from large-v3 on Linux. |
| 2. Diarisation | `pyannote.audio` 3.3.x with `pyannote/speaker-diarization-3.1`, `min_speakers=2, max_speakers=4`, behind a `Diarizer` protocol. If the capture is stereo or dual-mic, use channel-based attribution and skip diarisation. | `pyannote/speaker-diarization-community-1` (pyannote.audio 4.x): likely better, but CC-BY-4.0 and a heavier dependency jump. | Reject Sortformer as the default: `nvidia/diar_sortformer_4spk-v1` is CC-BY-NC-4.0 and capped at 4 speakers (verify the streaming v2 licence). |
| 3. Role assignment | Classify **per speaker cluster, once per encounter**, not per segment. Labels are `clinician`, `patient`, `companion`, `other`. A lexical baseline (question ratio, second-person and imperative rate, drug-term density) runs alongside the extraction LLM with a constrained enum and a confidence. Disagreement or low confidence means abstain and ask the user. | Clinician voice enrolment (embedding match). More robust, but it stores biometrics (GDPR Art. 9, BIPA), so it is an opt-in plugin only. | Segment-level classification is noisy and can flip mid-visit. |
| 4. Extraction | `Qwen/Qwen3-14B` (Apache-2.0), thinking disabled, temperature 0. One client against the OpenAI-compatible `response_format: json_schema`. Backends: **vLLM** (XGrammar) on Linux GPU and **llama.cpp `llama-server`** (GGUF Q4_K_M/Q5_K_M, Metal) on Mac. Ollama works but is not CI-tested. | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` (Apache-2.0); `Qwen/Qwen3-32B` if there is at least 48 GB VRAM. | See the extraction rules below. vLLM does not use the Apple GPU, so two backends are unavoidable. |
| 5. Validation | HL7 validator (`org.hl7.fhir.core`, Apache-2.0) run as a warm service via `hapifhir/org.hl7.fhir.validator-wrapper`; the CLI is for CI. Pin `hl7.fhir.us.core#6.1.0` and the validator version. | Matchbox (`ahdis/matchbox`, Apache-2.0) or HAPI JPA `$validate`. | JVM plus IG load takes tens of seconds, so no CLI call per resource. The default terminology server is `tx.fhir.org`, which breaks the self-hosted and offline requirement. |
| 6. Rendering | Jinja2 templates; every line carries resource id, evidence span and audio offset. | None worth considering. | See the rendering rules below. |

**Extraction rules (stage 4)**
- **The LLM never emits codes.** It emits mention text, a verbatim evidence quote with segment ids, and attributes: `assertion` (present/negated/possible), `experiencer` (patient/family/other), `temporality`, and `source_role`.
- **Every clinical field is nullable.** Required fields under constrained decoding force the model to invent doses and dates.
- **Schemas use the lowest common subset** of XGrammar and GBNF: no `$ref` recursion, no regex, bounded arrays, enums.
- **Always re-validate with pydantic v2**, and reject any fact whose quote is not a fuzzy substring of the transcript.

**Linking and mapping (between stages 4 and 5)**
- **Vitals:** a fixed LOINC table (8480-6, 8462-4, 8867-4, 8310-5 and so on) with UCUM units.
- **Medications:** RxNorm, with candidates retrieved from a local index (SQLite FTS5 plus `rapidfuzz`) and the LLM picking from at most 10 candidates or "none".
- **Conditions:** ICD-10-CM (public domain) by default; SNOMED CT only if the user supplies a release.
- **No match:** emit `code.text` only and flag it. The bindings are extensible, so the validator warns.
- **Statuses are conservative:**
  - New orders: `MedicationRequest.status=draft`.
  - Patient-reported medications: `intent=plan` with `reportedBoolean=true`.
  - Conditions and allergies: `verificationStatus=unconfirmed` or `provisional`.
- **Patient, Encounter and Practitioner** are injected from context, never extracted.

**Validation rules (stage 5)**
- **Terminology tiers:** tier 0 is `-tx n/a` plus our local code tables, labelled "terminology not fully checked". Tier 1 is a self-hosted HAPI FHIR JPA server (or Snowstorm for SNOMED) loaded with user-licensed content.
- **`fhir.resources` is not an authority.** Versions 7 and later ship R5 and R4B, not R4 4.0.1 (verify). Use our own pydantic builders, with `fhir.resources.R4B` as an optional pre-check at most.
- **One repair loop at most** (validator errors go back to the mapper or LLM), then the resource goes to review.

**Rendering rules (stage 6)**
- **The note will be thin, and we should say so.** Four resource types give no HPI narrative, exam prose, or follow-up and referrals; the note prints "not captured by Auris" stubs for these.
- **Resources that fail validation are never silently dropped.** A missing allergy is worse than a flagged one, so they appear in a "Needs review — not validated" block.

## (b) Licensing and redistribution traps

**Model weights**
- **Never vendor weights** in the repo, wheel or Docker image. Fetch by pinned revision hash, set `HF_HUB_OFFLINE=1` afterwards, and ship a `MODEL_LICENSES.md`.
- **Gated pyannote models** need a Hugging Face token and accepted terms. Fork CI and air-gapped installs break, so CI needs a non-gated path (recorded diarisation fixtures) and the docs need an offline-cache procedure.
- **Non-commercial weights are excluded:** Sortformer v1 and `nvidia/canary-1b` (CC-BY-NC).
- **Custom-licence weights stay out of defaults:** Llama-family (including OpenBioLLM and Meditron), Gemma and MedGemma (Health AI Developer Foundations terms).
- **Nothing trained on MIMIC or n2c2.** PhysioNet's data use agreement restricts sharing derived models.
- **CC-BY-4.0 weights** (community-1, Parakeet, WeSpeaker embeddings) are fine but need attribution in NOTICE.

**Terminologies**

| Terminology | Status | Action |
|---|---|---|
| SNOMED CT | Affiliate licence required | Never redistribute |
| CPT | Proprietary (AMA) | Never use |
| Full RxNorm | UMLS licence | Ship a download script; consider only the "Current Prescribable Content" subset (verify terms) |
| LOINC | Free, with a mandatory notice | Ship only the small vitals table with the notice |
| ICD-10-CM | Public domain | Default for Conditions |
| scispaCy UMLS linker, QuickUMLS | Embed UMLS content | Do not depend on them |

**Datasets**
- **ACI-Bench** is CC-BY-4.0 (verify) and, as far as I know, transcripts and notes only.
- **PriMock57** is permissive per its repo (verify whether that covers the audio) and is mock telehealth with separate channels.
- **Use download scripts**, not copies. Our annotations are released as CC-BY-4.0 stand-off files keyed to dataset ids.

**Code and build**
- **WhisperX history** includes a BSD-4-Clause period; check the pinned tag.
- **ffmpeg:** use the system binary or PyAV's LGPL build, never a GPL build in our image.
- **CI gate:** `pip-licenses` with an allowlist, plus `reuse lint`.
- **Not a licence but put it in the README:** not a medical device, clinician sign-off is mandatory, no telemetry.

## (c) Three-slice roadmap

**Slice 1 — Transcript to validated Bundle to SOAP (text in)**
- **Stubbed:** ASR, diarisation and roles, using human transcripts with gold speakers. Terminology is tier 0.
- **Real:** reduced schemas, extraction, evidence check, linker, mapper, validator service, renderer, review block, and the eval harness skeleton.
- **Demo:** `auris run transcript.json` produces `bundle.json`, `validation.json` and `note.md`.
- **Why first:** the project risk is in extraction and conformance, not in Whisper.

**Slice 2 — Audio in (batch)**
- **Real:** faster-whisper, pyannote and the role classifier on PriMock57 audio, with ASR confidence propagated to facts and abstention paths.
- **Stubbed:** streaming, multi-language, GPU deployment.
- **Demo:** a WAV file produces a role-labelled transcript and the Slice 1 outputs, with a cascade report (gold transcript, ASR with gold roles, full pipeline).

**Slice 3 — Clinic-realistic with clinician review**
- **Real:** a minimal review UI (Gradio, Apache-2.0) with accept, edit and reject plus click-to-audio evidence; `docker compose` for Linux with vLLM; tier 1 terminology; the acoustic stress set; published eval numbers.
- **Stubbed:** EHR write-back (POST the Bundle to a local HAPI test server) and voice enrolment.

## (d) Evaluation plan

**Gold annotation protocol**
- **Annotators fill the reduced fact schema**, not FHIR JSON, with evidence spans, in Label Studio (Apache-2.0). They work from human transcripts, plus audio where it exists.
- **No pre-annotation by our LLM**, to avoid anchoring.
- **Codes are picked from the same terminologies**, with "no code" allowed.
- **The deterministic mapper converts facts to FHIR.** Because gold and system share the mapper, it is checked separately: unit tests, the validator, and a FHIR-literate hand audit of about 30 resources per type.
- **Test set:** all 57 PriMock57 dialogues plus 60 ACI-Bench test dialogues (20 from each test split), 117 in total, each double-annotated by clinically trained annotators with a third adjudicating.
- **Dev set:** 20 ACI-Bench train dialogues. Prompts are tuned only there.
- **Budget:** about 150 annotator-hours.
- **Private split:** hold 30 test dialogues' annotations back with a canary string.

**Inter-annotator agreement**
- **Detection:** pairwise F1 on fact detection, because kappa is ill-defined without enumerable negatives.
- **Attributes:** Cohen's kappa on matched facts (assertion, experiencer, status).
- **Codes:** exact and relaxed agreement (RxNorm ingredient, ICD-10-CM 3-character category).
- **Freeze gate:** after a 10-dialogue pilot, guidelines freeze only at detection F1 ≥ 0.80 and kappa ≥ 0.70.
- **Reporting:** agreement is published per resource type as the human ceiling.

**Metrics**
- **Per stage:** WER and medication-name recall; diarisation error and word-level diarisation error; role accuracy.
- **Extraction:** strict and relaxed fact precision, recall and F1 per resource type.
- **Critical errors, counted separately:** flipped allergy negation, wrong drug or dose, wrong experiencer, unsupported facts.
- **Validator:** pass rate with the terminology tier stated, always with the caveat that pass does not mean correct.
- **Uncertainty:** bootstrap confidence intervals over dialogues, with an explicit statement that n is small.

**Using note targets without faking FHIR gold**
- **Silver signal only:** deterministic RxNorm and ICD dictionary matches on reference notes give a medication and problem recall proxy. It is labelled silver and never mixed into headline numbers.
- **Human coverage rating:** on a 30-note sample, raters score how much of the reference note our rendered note covers.
- **No ROUGE or BERTScore as quality.** A template note against a prose note is meaningless.

**Audio realism**
- **Benchmark limits:** PriMock57 is clean mock telehealth and ACI-Bench is mostly text.
- **Stress set:** replay PriMock57 channels through two loudspeakers in a real exam room to a phone at 1–2 m, plus MUSAN and room-impulse-response augmentation. It is labelled simulated.
- **Real clinic audio** comes only via a partner with consent and ethics approval, and is reported separately.
- **No TTS and no same-family synthetic dialogues** outside smoke tests.

## (e) Top 5 failure modes in real clinic audio

1. **Far-field, overlapping or masked speech and room noise cause deletions and speaker confusion.**
   - Publish capture guidance: two mics or a stereo boundary mic, which enables channel attribution.
   - Propagate `avg_logprob`, `no_speech_prob` and overlap flags to each fact.
   - Send low-confidence regions to review.
   - Stratify WER by accent.
2. **Whisper hallucinates during silence, typing or exam pauses, and those sentences become invented facts.**
   - Use VAD gating and `hallucination_silence_threshold`, with no conditioning on previous text.
   - Require a verbatim evidence quote for every fact.
   - Run a second-opinion ASR (Parakeet) on segments backing medications and allergies; disagreement flags the fact.
3. **Sound-alike drugs and numbers are misheard (hydroxyzine/hydralazine, 15/50).**
   - Link through RxNorm with phonetic candidates.
   - Check dose plausibility against RxNorm strengths.
   - Never auto-correct silently.
   - Keep every MedicationRequest in `draft` until confirmed, with an audio snippet attached.
4. **Wrong experiencer, negation or temporality** (companion speaking, "my mother has diabetes", "no penicillin allergy", "if it worsens we'll start…", third speakers).
   - Carry explicit attribute fields and a `companion`/`other` role.
   - Do not map family history to Condition.
   - Maintain a contrastive regression suite.
   - Treat any allergy-negation flip as a release blocker.
5. **Unspoken or partial information gets filled in** (silent vitals, vague "the blue pill", missing dose).
   - Nullable fields with an explicit `not_stated`.
   - The mapper omits rather than defaults.
   - The note prints "not captured".
   - For long visits, chunk with overlap and merge duplicates deterministically.

## Top 3 disagreements I expect

1. **Slice 1 is text-in with audio stubbed.** Others will want audio first because the product is "ambient"; I hold that extraction and conformance carry the risk.
2. **The LLM emits no codes, and ICD-10-CM plus RxNorm (not SNOMED) are the defaults.** Tier 0 terminology is openly labelled incomplete; others will want SNOMED and `tx.fhir.org` for higher pass rates.
3. **WhisperX, Ollama and `fhir.resources` leave the core path, and gold is a clinician-authored fact schema through a shared mapper.** Others will argue for convenience tooling and for hand-authored FHIR gold.
