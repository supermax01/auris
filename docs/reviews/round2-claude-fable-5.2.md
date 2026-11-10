# Round-2 critique: merged draft v1 (Fable 5.2)

## Verdict: BLOCK (narrow, about a day of doc work)

**Strongest reason:** the output contract has no US Core profile and status matrix. The draft never says which profile each resource claims in `meta.profile`, what each assertion kind maps to, or which required fields come from where. The annotation schema, the mapper, the validation pass rate and the Slice 1 exit all depend on that table, and two v1 statements already contradict it (A1, A2). A draft matrix is in A3; adopt or edit it and I move to LOCK.

Second reason, for the owner: the ~150 annotator-hour figure is wrong by about 2.5× (Q3).

Everything below is from memory (knowledge to mid-2026) with no tools used, so "confident" means confident recollection, not checked.

## Q1. Diarisation D1: ratify dual-profile, with four changes

1. **The demo should not depend on diarisation.** Record the commissioned fixture dual-channel so it uses the channel-attribution bypass; ship a mono mixdown as the diariser test.
2. **The pyannote profile is stale.** pyannote.audio 4.x ships `speaker-diarization-community-1` (CC-BY-4.0, still gated, I believe), reported as clearly better than 3.1. Pin that or justify 3.1. 4.x also moved audio decoding to torchcodec, which needs FFmpeg (see Q4).
3. **Silero + ECAPA + AHC is a diariser we write and own.** It has no overlap handling, and its clustering threshold needs a tuning set that is not PriMock57 or ACI (AMI or VoxConverse; verify licences). Name it in the plan.
4. **Decide the default on downstream metrics, not DER.** Compare assertion F1 and role accuracy across oracle, portable, pyannote and a "no diariser, LLM assigns turns from text" baseline. The portable profile stays default if it is within a predeclared margin (I propose 2 F1 points) of pyannote.

PriMock57 audio is, as I recall, separate per-speaker channel files. If so, diarisation there is evaluated on an artificial mixdown, which must be labelled, and the gold speaker timeline comes free. Verify.

## Q2. Extractor D2: 8B default is right for a 16 GB laptop

Qwen3 8B at Q4_K_M is about 5 GB; 14B is about 9 GB and does not sit alongside ASR and a JVM validator.

Flip the default to 14B only if all three hold on **dev** data, with test only reporting:
- **Quality:** critical errors (negation, experiencer, unsupported order) fall by a predeclared relative margin (I propose ≥25%), or assertion F1 rises ≥3 points with an encounter-bootstrap CI excluding 0.
- **Memory:** peak RSS stays within the stated laptop envelope with models loaded sequentially.
- **Runtime:** a 10-minute encounter stays under a stated wall-clock ceiling.

Two gaps:
- **Dev set too small.** 20 dev encounters cannot resolve a 3-point difference. Enlarge dev (ACI-Bench's official 20-dialogue validation split plus some train) or state that the default is provisional.
- **Thinking mode undecided.** Qwen3 is a hybrid-thinking model and a `<think>` block collides with grammar-constrained JSON. Decide: thinking off for extraction, verified per backend.

Add `gpt-oss-20b` (Apache-2.0, confident) as a comparator; Phi-4 (MIT) and Granite (Apache-2.0) are worth checking.

## Q3. Gold D4/D5: structure acceptable, budget not

**D4:** accept fact-level primary plus the 30-encounter FHIR subset, with two amendments.
- Stratify the 30 by dataset and resource type; do not sample randomly, because allergies will be rare.
- Have the FHIR specialist encode from the adjudicated facts, not the transcript. That isolates mapper bias, which is the thing being audited.

**D5:** the 150 hours is attributed to my draft and does not survive arithmetic. 117 encounters × 2 annotators in 150 hours is 38 minutes each, with nothing for anything else. My estimate:

| Item | Hours |
|---|---|
| Training and up to 3 pilot rounds | ~25 |
| Main pass, 117 × 2 × ~1.0 h | ~235 |
| Adjudication, 117 × ~0.4 h | ~45 |
| Dev set, 20 × 2 × ~0.75 h | ~30 |
| FHIR subset, 30 × ~1 h plus 10 second-reviewed | ~40 |
| **Total** | **~375** |

Put two envelopes to the owner: about 375 hours for the full design, or about 190 hours for a reduced test set (30 PriMock57 + 30 ACI) with wider CIs stated up front.

Other evaluation gaps:
- **The "30 held private" set has no source.** It cannot come from public corpora; it must be newly commissioned, with cost, consent and release terms.
- **PriMock57 is UK primary care.** Drug names (paracetamol, salbutamol) and practice are UK, while the targets are RxNorm, ICD-10-CM and US Core. Code-level metrics will be confounded. Stratify every code metric by dataset and declare a UK-to-US synonym policy.
- **ACI-Bench audio coverage.** I believe not every dialogue has audio. If so, those are text-only items and cascade ablations run on a subset. Verify.
- **Contamination.** Both corpora are public and probably in LLM training data. Say so next to every result.
- **Stand-off annotations.** If they contain verbatim evidence quotes they are not stand-off. Store offsets and hashes.
- **IAA gate failure path.** Add: after 3 pilot rounds, narrow the schema; never lower the bar.

## Q4. Licence, version and existence claims

| Claim in v1 | Status | Note |
|---|---|---|
| Allergies `unconfirmed/provisional` | **Wrong** (confident) | R4 AllergyIntolerance verification codes are unconfirmed, confirmed, refuted, entered-in-error. `provisional` exists only for Condition. |
| `fhir.resources` "VERIFY R4 support" | **Likely wrong** | Current releases cover R5, R4B and STU3. R4 4.0.1 lived in old 6.x on pydantic v1. Recommend dropping it. |
| "FFmpeg (LGPL build only) later" | **Wrong on timing** | faster-whisper depends on PyAV, whose wheels bundle FFmpeg libraries from day one. Debian's `ffmpeg` is a GPL build, so the Docker image needs a decision. Verify the PyAV wheel build config. |
| RxNorm "user-imported approved subset" | **Too conservative** | NLM's RxNorm Current Prescribable Content needs no UMLS licence, as I recall. Download-at-setup would let the one-command demo code medications. Verify terms. |
| Sortformer NC vs CC-BY | **Per checkpoint** | My recollection: `diar_sortformer_4spk-v1` is CC-BY-NC-4.0; the streaming v2 checkpoint is NVIDIA Open Model License. It also caps at 4 speakers and needs NeMo. Recommend out of v1 scope. |
| "No telemetry" | **Unmet as written** | vLLM collects usage stats by default (`VLLM_NO_USAGE_STATS=1`), and HF hub has its own telemetry flag. The FHIR validator contacts tx.fhir.org and packages.fhir.org unless `-tx n/a` and the package cache is pre-seeded. |
| US Core 6.1.0 | Defensible | It is the USCDI v3 / HTI-1 baseline. State that rationale; newer versions exist (7.0, 8.0), so make it configurable. |
| US Core dependencies offline | Verify | 6.1.0 pulls `us.nlm.vsac` and `hl7.terminology`. Check redistribution before baking the cache into a Docker image. |
| LOINC vitals table with notice | OK | UCUM units also carry terms; add to NOTICE. |
| ICD-10-CM for conditions | OK | US Core condition codes are extensible and include ICD-10-CM. |
| Allergy coding at tier 0 | Gap | Non-drug allergens and "no known allergies" are SNOMED concepts, so they are text-only or uncoded without SNOMED. |
| faster-whisper, CTranslate2, Whisper weights MIT | Confident | Two caveats: `hallucination_silence_threshold` requires `word_timestamps=True`, and CTranslate2 has no Metal backend, so large-v3 on a Mac is CPU-slow. Add large-v3-turbo (MIT) as a candidate. |
| SpeechBrain Apache-2.0, Silero VAD MIT | Confident for code | Verify the ECAPA checkpoint card and VoxCeleb provenance. |
| Qwen3 Apache-2.0, 8B/14B/32B exist | Confident | Check whether a newer Apache family has shipped since my cutoff. |
| medspaCy MIT | Confident | Check its spaCy and Python pins against our environment. |
| WhisperX BSD-2, Medplum Apache-2.0, OpenEMR GPL-3.0 | Consistent with memory | |
| PriMock57 and ACI-Bench licences | Not stated | I recall CC-BY-4.0 for ACI-Bench; PriMock57 needs checking, and Babylon is defunct, so pin commit and checksum. |
| HealthSage "does not resolve" | Check Hugging Face | I recall a `healthsageai/note-to-fhir-13b-adapter` model (Llama-2 LoRA, text to FHIR). |
| "Space is small", leaders 19–32★ | Search likely missed projects | From memory: ClinicianFOCUS/FreeScribe, bloodworks-io/phlox, 1984Doc/AI-Scribe are local-first scribes with more traction. Google's MedASR/MedGemma (custom terms) is a comparator to name. Verify all. |
| "OSS-model reliability by construction" | Overclaim | Constrained decoding gives syntactic validity only. Write "by design, to be demonstrated". |
| "Not a medical device" | Reword | A README cannot settle classification. Say "not cleared or approved; not intended for clinical use without review". |

## Q5. The missing decision, and amendments

**A1.** Fix allergy statuses (table above).

**A2.** Item 5 extracts medication intent `history/continue`, while item 8 prints historical meds as "not captured". Pick one.

**A3. Draft profile matrix** (my proposal; cardinalities to be checked against the 6.1.0 StructureDefinitions by the FHIR specialist):

| Assertion | Profile | Fixed values | Emit only if |
|---|---|---|---|
| Vital stated with value | US Core vital-sign profiles | `status=preliminary`, UCUM unit, `effectiveDateTime` = encounter time only if measured in-visit | value, unit and time resolved; else review |
| Labs, smoking status, symptoms as Observation | none in v1 | n/a | logged out-of-scope |
| Clinician-assessed problem | condition-encounter-diagnosis | `provisional` | clinician is speaker or confirms |
| Patient-reported condition | condition-problems-health-concerns | `unconfirmed` | experiencer = patient |
| New spoken order | MedicationRequest | `status=draft`, `intent=proposal` until review, requester = supplied Practitioner | drug resolved; dose text verbatim |
| Reported or continuing med | MedicationRequest | `reportedBoolean=true`, `intent=plan`, `status=active` only if current use is stated, else `unknown` | n/a |
| Discussed or unresolved med | no resource | n/a | review queue |
| Allergy | us-core-allergyintolerance | `clinicalStatus=active`, `unconfirmed` | explicit statement; never inferred from silence |

Consequences of the matrix:
- Add an optional supplied **Practitioner** anchor.
- The mapper always sets `meta.profile`; otherwise the validator checks base R4 only and the US Core pass rate means nothing.
- "Symptoms out of scope" leaves the S section nearly empty. Either scope symptoms in, or rename the artefact "structured encounter summary" so it is not judged as a bad SOAP note.

**A4. Evidence chain.**
- Define the normalisation for the substring check, the failure path, and quotes that span turns. I suggest the LLM emits segment ids from a constrained enum plus the quote.
- Label the result "transcript-supported", not "verified". A hallucinated ASR span passes the check.

**A5. Other undefined items.**
- "Material assertion" needs a definition; I propose any medication, allergy, negated or non-patient-experiencer assertion.
- Minimum laptop spec, sequential load order, and JVM provisioning on a Mac (mise).
- Which warm validator mechanism (validator-wrapper or the CLI's server mode).
- `n_ctx` and the long-encounter chunking policy.
- Slice 3 POSTs a `collection` Bundle, which a server stores as a blob. The exporter needs a `transaction` form.

**A6. Slices.**
- Move the network-disabled test from Slice 3 to Slice 1 CI.
- Give Slices 1 and 3 measurable exits.
- The Slice 2 review UI has no stack or size. Set the minimum as a static HTML report with audio anchors plus JSON-patch edits, with an interactive UI as stretch.
- The commissioned audio needs signed voice releases and an explicit licence.

**A7.** Gemini's round-1 input is still missing. Record in PLAN.md that the lock was made on three of four drafts.
