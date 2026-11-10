# Competitive analysis — OSS ambient scribe / FHIR extraction (2026-11-10)

Method: `gh search repos` (ambient scribe / medical scribe whisper / SOAP note
generation / text-to-FHIR), sorted by stars; READMEs of the leaders read via
GitHub API. Star counts are as of this date; the space is small and fragmented.

## What exists

| Repo | Accomplished | Limits Auris exploits |
|---|---|---|
| [j-frei/Infherno](https://github.com/j-frei/Infherno) (19★, Python, no licence) | Closest prior art. Text notes → FHIR agent (SmolAgents), SNOMED RAG; EACL 2026 system demo, public demo. | Text-only input. README recommends proprietary Gemini Pro 2.5 and states local models were "substantially less reliable"; needs a Snowstorm terminology server. Unlicensed code. |
| [bloodworks-io/phlox](https://github.com/bloodworks-io/phlox) (110★, MIT) | Local-first AI scribe + patient management, live agent, custom note templates; requires external OpenAI-compatible inference/transcription endpoints (Docker has none built in). Added after round-2 review caught that earlier searches missed it. | Notes/letters output; README shows no validated FHIR export or shipped eval harness. Also: ClinicianFOCUS/FreeScribe (15★, AGPL) and 1984Doc/AI-Scribe (52★, GPL) — local SOAP scribes, copyleft. |
| [trevorpfiz/scribeHC](https://github.com/trevorpfiz/scribeHC) (26★, TypeScript, MIT) | Working OSS ambient-scribe web app: record → transcript → SOAP note, with demo. | Note-only output, no FHIR, no validation, no evaluation harness. Cloud-API oriented. |
| [yuki-2025/MediNotes](https://github.com/yuki-2025/MediNotes) (32★, Python, no licence) | UChicago capstone: fine-tuned Llama-3/Mistral dialogue→SOAP models on HF, Streamlit demo. | Research capstone, note-only, no FHIR, no deploy path, unlicensed. |
| [junqiu-lei/ambient-scribe](https://github.com/junqiu-lei/ambient-scribe) (MIT) | Local-first psychotherapy scribe: faster-whisper + Ollama llama3.1, deletes audio, exemplary consent/HIPAA scope language. | Single-specialty, note-only, no FHIR, no eval. Its privacy/consent framing is the bar to match. |
| [Arkapravo-Ghosh/totalscribe](https://github.com/Arkapravo-Ghosh/totalscribe) (AGPL-3.0) | Next.js/FastAPI audio→SOAP app. | Note-only; "HIPAA compliant" marketing claim with no evidence; AGPL deters embedding. |
| Text-to-FHIR utilities (e.g. text2phenotype/fhir-hydrant, Apache-2.0) | Rule/NLP text→FHIR converters. | No audio, no LLM extraction, no encounter workflow. |

Searches for "clinical note FHIR LLM" and "audio to FHIR" return nothing of
substance: **no OSS project found does audio → validated FHIR**.

## How Auris one-ups each axis

1. **FHIR-first, not note-first.** Most inspected ambient-scribe apps emit
   only a note; Auris emits validated FHIR resources and derives the note.
   (vs scribe apps; Infherno/text-to-FHIR tools are text-in)
2. **OSS-model reliability by construction.** Constrained decoding + reduced
   schemas + validator feedback loop, precisely where Infherno gave up on
   local models and recommends a proprietary API. (vs Infherno)
3. **Audio end-to-end.** ASR + diarisation + explicit role assignment.
   (vs Infherno, text-to-FHIR tools)
4. **Validation as a stage, honestly.** US Core profile validation with
   structured errors returned for repair/review — and a published pass rate,
   not "guarantee"/"HIPAA compliant" claims. (vs totalscribe marketing)
5. **Evaluation people can rerun.** Shipped harness, gold-FHIR annotation
   protocol with IAA, honest baselines on ACI-Bench/PriMock57 (note targets
   stated as such). No competitor ships this. (vs all)
6. **Local-first deployment.** Offline capable, audio deleted by default,
   consent/scope docs at junqiu-lei's standard, Docker + laptop demo.
   (matches best, exceeds app demos)
7. **Permissive licence + swappable stages.** Apache-2.0/MIT code, diariser
   behind an interface because pyannote weights are gated and NVIDIA weights
   vary by model licence. (vs unlicensed capstones, AGPL app)
