# Auris — Final Plan (locked for owner review, 2026-11-10)

Team: Muse (coordinator), Claude Code CLI `claude-fable-5-2`, Muse
`gpt-6.1-sol` (reasoning max), Gemini via Chrome (human relay). Rounds:
independent drafts → coordinator merge → round-2 critiques (Fable and Codex
both BLOCKED on the missing export matrix) → Gemini review via Chrome
(BLOCKED on vital-sign timestamp, BP-component, and negation traps inside
that matrix; its four amendments are applied in §3, Approach, Validation,
and Positioning below) → coordinator verification of every flagged
licence/FHIR claim (see Sources). All four teammates have now reviewed.
No application code exists yet. Owner approval ("go") is required before
implementation.

## Goal

Open-source, self-hostable ambient scribe: clinician–patient audio →
validated FHIR R4 resources (Observation, Condition, MedicationRequest,
AllergyIntolerance) → a SOAP-format encounter summary rendered from those
resources. Aim: the OSS tool of record for audio-to-FHIR in work, research,
and deployment — won on structured output, honest validation, rerunnable
evaluation, and local-first operation, not marketing claims.

## Success Criteria

1. One command turns a sample WAV into `bundle.json`, `validation.json`,
   and `note.md` on a laptop, fully offline after one-time model download.
2. Every exported resource is evidence-linked (quote, audio offset,
   speaker) and labelled by state: extracted / structurally valid /
   profile-and-terminology checked / clinician-reviewed. No state implies
   the next; no output claims more than its lane results support.
3. A stranger can rerun our published evaluation and reproduce our numbers.
4. Any stage (ASR, diariser, extractor, validator) can be swapped without
   touching the rest of the pipeline.

## Approach

Pipeline: ingest → ASR → diarisation → role assignment → constrained
extraction into a reduced assertion schema → deterministic terminology
mapping → FHIR validation lanes → human review → deterministic rendering.

- **ASR:** faster-whisper (verified MIT). Linux quality profile large-v3;
  laptop profile INT8 CPU. VAD gating, no conditioning on previous text,
  word timestamps labelled approximate. ASR scores are flags, never
  calibrated clinical probabilities.
- **Diarisation:** one interface, two profiles. Default portable profile:
  Silero VAD + SpeechBrain speaker embeddings + clustering — ours to own
  (windows, speaker-count estimation, short-turn and overlap handling,
  `unknown` preserved; tuned on a non-test tuning set). Comparison profile:
  pyannote speaker-diarization-community-1 (verified CC-BY-4.0, gated
  access; user accepts terms and supplies token; offline cache import must
  be demonstrated). Demo fixture is dual-channel so the demo never depends
  on diarisation quality. Profile choice is later decided on downstream
  assertion F1/role accuracy (portable stays default within 2 F1 points),
  not DER alone.
- **Roles:** separate component; per-cluster clinician / patient /
  companion / other / unknown, correctable per segment in review.
  Lexical baseline + extractor-LLM proposal; disagreement → review.
- **Extraction:** Qwen/Qwen3-8B (verified Apache-2.0) as the initial laptop
  candidate, thinking mode off, temperature 0; llama.cpp on Mac,
  vLLM+XGrammar on Linux (all verified permissive). Backend contract tests
  must prove AST-level schema conformance and identical rejection of
  invalid structures on both backends (token streams will not be
  bit-identical across Metal/CUDA/CPU; that is accepted).
  The LLM never emits codes and never fills missing facts: it emits atomic
  assertions with a mandatory transcript evidence span (segment id +
  quote, verified against the immutable transcript by case- and
  punctuation-normalized token-span alignment resolving exact
  character/audio offsets; unaligned quotes fail the evidence lane;
  aligned results labelled "transcript-supported", never "verified"),
  subject, negation, experiencer, temporality, and medication
  intent. Every clinical field is nullable. Flip to a larger extractor only
  on predeclared dev-set criteria (≥25% relative cut in critical errors, or
  ≥3 F1 points with CI excluding 0, within laptop memory/runtime budgets),
  measured at declared coverage; if all candidates fail, none is default.
- **Mapping:** deterministic code, local candidate retrieval, LLM picks
  from candidates or none. Defaults: LOINC vitals (small table + notice),
  ICD-10-CM for conditions, RxNorm Current Prescribable Content downloaded
  at setup (NLM states no licence required). SNOMED CT only via
  user-supplied licensed release. No match → text-only code + flag, never
  an invented code.
- **Rendering:** deterministic templates only, from accepted resources plus
  a flagged "Needs review — not validated" block. Unsupported encounter
  content is recorded in the evidence ledger and shown as "discussed, not
  captured as FHIR"; absent content is shown as "not captured". The
  artefact is a structured encounter summary in SOAP format — four
  resource types cannot represent a full clinical note, and the plan does
  not claim otherwise.

## §3 Export and acceptance matrix (the contract both reviewers required)

`meta.profile` is always set by the mapper; a resource without it claims
base R4 only. Intake context MUST supply Patient, Encounter (including
`Encounter.period.start`), and a default Practitioner anchor — a
MedicationRequest is never exported without `requester` bound to that
anchor. Anchors are never extracted or invented. Validation lanes report
PASS / FAIL / NOT_CHECKED separately (reduced schema → evidence and
consistency → base R4 → declared US Core profiles, pinned
`hl7.fhir.us.core#6.1.0` for compatibility, configurable → terminology),
parsed lane-by-lane from validator OperationOutcome severities.
Tier 0 terminology (local tables, validator terminology disabled) always
sets the terminology lane to NOT_CHECKED and can never produce a
"fully validated" label; reviewed state is invalidated by any edit;
validator feedback may only trigger evidence-grounded structural repair
(max one loop), never invented clinical facts.
Profile cardinalities and invariants (vital-signs effective-time rules,
requester, category slices) are checked against the 6.1.0
StructureDefinitions by the FHIR specialist as Slice 1's first task;
rows below are the contract that check must confirm or amend.

| Assertion | Polarity | Resource / profile | Fixed semantics & mapper rules | Export rule |
|---|---|---|---|---|
| Vital sign (value+unit) | Affirmed | Observation, specific US Core vital profile | `status=preliminary`, UCUM unit. Blood pressure = panel 85354-9 with systolic/diastolic in `component`, never root `value[x]`. Unspoken measurement time deterministically inherits `Encounter.period.start` with a provenance note (context inheritance, not an extracted fact) | Export if lanes pass; else review |
| Symptom | Affirmed/negated | No resource in v1 | Ledger entry with polarity; rendered under S as transcript-supported text (pertinent positives/negatives) | Review-visible; not claimed as FHIR |
| Clinician-assessed problem | Affirmed | Condition (encounter-diagnosis) | `verificationStatus=provisional` | Only if clinician stated/confirmed it |
| Clinician-ruled-out problem | Negated | Condition (encounter-diagnosis) | `verificationStatus=refuted`, `clinicalStatus` omitted | Review queue by default; export only after clinician confirmation |
| Patient-reported condition | Affirmed | Condition (problems/health-concerns) | `verificationStatus=unconfirmed` | Experiencer must be patient; negated history stays in the ledger |
| Explicit new spoken order | Affirmed | MedicationRequest (US Core) | `status=draft`, `intent=proposal` until review, `requester` = intake Practitioner, dose verbatim or absent | Ambiguous/negated discussion never becomes a request; Auris never activates/signs |
| Reported/continuing medication | Any | No MedicationRequest in v1 | Ledger + review queue (MedicationStatement is a deliberate post-v1 scope decision) | Never exported as an order |
| Allergy statement | Affirmed | AllergyIntolerance (US Core) | `verificationStatus=unconfirmed` (R4 codes are only unconfirmed/confirmed/refuted/entered-in-error — verified) | Explicit statement only; non-drug allergens text-only set terminology lane NEEDS_CODING_REVIEW |
| Allergy refutation / NKA | Negated | No AllergyIntolerance without a valid refutation code (SNOMED-dependent) | Ledger + review; rendered under S as transcript-supported denial | Never exported as `unconfirmed` (that would invert its meaning) |

"Material assertion" = any medication, allergy, negated/refuted, or
non-patient-experiencer assertion. Material + unmapped or
validation-failing ⇒ final export blocked until review resolves or
dismisses it (preview still allowed; pertinent negatives cleanly routed
to the ledger do not block). Integration proof builds a self-contained
`transaction` Bundle with `urn:uuid:` fullUrls for the intake anchors
plus extracted resources, posted to a local R4 test server with reference
resolution tested; storing a collection Bundle proves storage only.

## Steps (each demoable; no step stubs extractor, mapper, or validator)

1. **Slice 1 — contract proof.** Text-in on human transcripts (oracle
   speakers/roles): schemas, extraction, mapping, §3 matrix, HL7 validator
   (pinned JAR, warm service, setup-seeded package cache), renderer, review
   block, eval-harness skeleton, network-disabled test in CI from day one.
   Then one commissioned fictional dual-channel audio encounter (signed
   voice releases, explicit licence) through real ASR with an oracle
   speaker timeline; one example per resource type plus one
   export-refusal case.
2. **Slice 2 — real speakers + review.** Portable diariser (+ pyannote
   comparison measured on frozen dev recordings), roles, overlap/unknown
   handling, minimal review surface (static report with click-to-audio
   evidence and structured edits; interactive UI is stretch),
   revalidate-and-rerender after edits. Exit is measured (see Validation),
   not a single demo refusal.
3. **Slice 3 — research + deployment.** Locked evaluation published with
   latency/memory/hardware; Linux CPU/GPU + Apple Silicon install paths;
   Docker one-command demo with published download size and measured
   runtime; air-gapped cache import and retention lifecycle (original
   audio, evidence playback, deletion/expiry reconciled explicitly);
   tier-1 local terminology; integration proof to a local test server.

Licensing throughout: Auris code Apache-2.0 (owner confirms at "go");
weights/terminology never vendored — published Docker images contain
code and runtimes only; models, RxNorm tables, and FHIR package caches are
fetched by a setup step into a mounted cache volume (with cache
export/import for air-gapped installs). Artifact manifest with source,
revision, checksum, licence, access conditions, and upstream dataset
provenance (e.g. VoxCeleb-trained speaker embeddings flagged as
academic-dataset provenance for enterprise deployers); NC and custom-licence
weights excluded from defaults (e.g. the verified CC-BY-NC-4.0 Sortformer
checkpoint is out of v1 scope); unknown training provenance is recorded,
not laundered by a permissive weight licence; dependency licence audit in
CI across Python/Java/native/model artifacts; telemetry off everywhere
(including inference-server and hub defaults). `fhir.resources` is dropped
(verified: current releases provide R5/R4B/STU3, not R4 4.0.1); our own
builders + the HL7 validator are the authority. README scope language:
research software, not cleared/approved for clinical use, clinician review
mandatory; regulatory classification is deployment-specific.

## Validation Plan

Evaluation is a shipped product surface.

- **Gold:** two clinically trained annotators, blind to system output, no
  LLM pre-annotation; fact-level annotation primary, plus a stratified
  30-encounter independent-FHIR subset encoded from adjudicated facts to
  isolate mapper bias; out-of-scope clinically important assertions also
  recorded. Annotations stored as offsets+hashes (evidence quotes would
  not be stand-off); release licence decided only after adaptation rights
  are confirmed per corpus.
- **Splits and leakage discipline:** PriMock57 recordings used in Slice 2
  development are excluded from held-out claims; ACI-Bench items without
  usable audio support text metrics only (verify at setup); a private set
  must be newly commissioned (public dialogues with withheld labels are
  not unseen, and both public corpora may be in LLM training data — stated
  beside every result). PriMock57 is UK primary care: code metrics are
  stratified by dataset with a declared UK→US synonym policy. Spoken
  physical vital signs may be sparse in mock/telemedicine corpora
  (distribution verified at setup): per-class denominator counts are
  always reported, any class with N < 25 test assertions is flagged
  low-power, and no headline class-level claim is made for it without a
  targeted vitals/orders stress suite.
- **Budget (owner decision at "go"):** full design ≈375 annotator-hours
  (117 test + 20 dev, double-annotated, adjudication, FHIR subset) or
  reduced ≈190 hours (30+30 test) with wider CIs declared up front. A
  timed 5–10 encounter pilot replaces both estimates with a formula.
  Guidelines freeze after pilot only at detection F1 ≥0.80 / kappa ≥0.70;
  if unmet after 3 rounds, narrow the schema — never lower the bar.
- **Metrics:** per-stage (WER + critical-token errors; DER/JER with
  settings; role accuracy/coverage/abstention), assertion P/R/F1 per
  class at declared coverage — computed by optimal bipartite (Hungarian)
  matching over (resource class, experiencer, polarity, clinical concept),
  with a secondary strict match requiring evidence-span overlap and code
  equivalence — medication-component exactness,
  unsupported-order rate, validation lanes separately, correction burden,
  encounter-level bootstrap CIs. Observed human agreement (not "ceiling")
  published per type. Cascade ablations: gold vs ASR transcript, oracle
  vs actual roles. Baselines: a rules/medspaCy pipeline we actually build
  (toolkit, not a ready baseline), unadapted extractor, larger extractor.
  Critical-error classes (allergy-negation flip, wrong drug/dose
  exported, wrong experiencer, invented order) are release blockers; the
  owner adjudicates failures. F1 ≥0.80 remains a target, not a result.

## Positioning (bounded, gh-inspected 2026-11-10; stars are dated snapshots)

Most inspected ambient-scribe apps are note-first (phlox 110★ local-first
agent requiring external inference endpoints, scribeHC 26★, MediNotes 32★
capstone, FreeScribe/AI-Scribe copyleft apps — inspected READMEs and repo
descriptions show notes, not validated FHIR export). Infherno (19★) does text→FHIR; its
README reports local models were substantially less reliable in their
experiments and recommends a proprietary model — constrained decoding +
validation is our proposed response, to be demonstrated, not a proven
win. Architecturally, Auris also avoids multi-step agentic tool loops
(single-pass constrained extraction + deterministic mapping instead), and
aims to be an open-source, zero-egress counterpart to AWS HealthScribe's
evidence-linked design, outputting native US Core R4 instead of
proprietary JSON. Platforms (Medplum, OpenEMR) are integration targets via FHIR, not
competitors to rebuild. Within these dated searches, no OSS
audio→validated-FHIR pipeline with a rerunnable evaluation was found;
that combination is Auris's claim to the category.

## Risks / Open Questions

- Owner at "go": Apache-2.0 confirmation; annotation envelope (375h vs
  190h); US-only terminology defaults vs UK data reality.
- Verify at pin time (recorded, not assumed): exact ASR artifact licences
  and conversions, ECAPA checkpoint card, US Core transitive package
  redistribution for offline Docker, per-dataset licences, PyAV/FFmpeg
  build obligations (present from day one via ASR dependencies).
- Real clinic audio requires a consented partner; until then all accuracy
  claims are mock-consultation claims, labelled as such.

## Sources

- https://hl7.org/fhir/R4/valueset-allergyintolerance-verification.html
- https://hl7.org/fhir/R4/validation.html
- https://huggingface.co/api/models/nvidia/diar_sortformer_4spk-v1
- https://huggingface.co/api/models/pyannote/speaker-diarization-community-1
- https://huggingface.co/api/models/Qwen/Qwen3-8B
- https://raw.githubusercontent.com/nazrulworld/fhir.resources/main/README.rst
- https://www.nlm.nih.gov/research/umls/rxnorm/docs/rxnormfiles.html
- https://github.com/SYSTRAN/faster-whisper
- https://github.com/snakers4/silero-vad
- https://github.com/speechbrain/speechbrain
- https://github.com/pyannote/pyannote-audio
- https://github.com/ggml-org/llama.cpp
- https://github.com/vllm-project/vllm
- https://github.com/mlc-ai/xgrammar
- https://github.com/hapifhir/hapi-fhir
- https://github.com/medspacy/medspacy
- https://github.com/j-frei/Infherno
- https://github.com/bloodworks-io/phlox
- https://github.com/trevorpfiz/scribeHC
- https://github.com/yuki-2025/MediNotes
- https://github.com/aws-samples/aws-healthscribe-demo
- https://github.com/medplum/medplum
- https://github.com/openemr/openemr
- https://github.com/ClinicianFOCUS/FreeScribe
- https://github.com/1984Doc/AI-Scribe
- https://chandravikram.com/papers/07-auris.html
