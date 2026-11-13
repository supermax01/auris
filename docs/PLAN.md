# Auris — The Plan

*Final plan for owner review · 2026-11-10 · No code is written until the
owner says "go".*

Reviewed in three rounds by four AI teammates (Muse as coordinator, Claude,
Codex and Gemini), then a round-4 review by Claude Fable 5.2 whose five
changes are applied. FHIR facts were checked against the official HL7 pages
([fhir-reference.md](fhir-reference.md)); every change is traced in
[reviews/](reviews/).

---

## 1. What we are building

**Auris listens to a recorded doctor–patient visit and turns it into
structured medical records that hospital software can read.** These are
the vital signs taken, the problems diagnosed, the medicines ordered and
the allergies mentioned. Each record is linked to the exact moment in the
recording it came from. From those records Auris writes a short visit
summary in the familiar SOAP layout (Subjective, Objective, Assessment,
Plan).

It is open source, it runs on your own laptop or server, and the audio
never leaves that machine.

**Who version 1 is for.** Researchers and health-IT developers. Version 1
is a research tool and a reference pipeline: it proves the approach and
measures it honestly. It is not yet a scribe a clinician would choose for
daily use, because symptoms and current medicines are not turned into
records and the summary is a templated list.

## 2. The problem

Writing up a visit is a large part of a clinician's day. "AI scribes" have
appeared to help, but the ones we inspected share four gaps:

1. **They write prose, not data.** Most produce a paragraph-style note. A
   note is easy to read but hard for software to use: an EHR (electronic
   health record) cannot reliably file "patient mentioned a penicillin
   rash" as an allergy record from a paragraph.
2. **They send audio to the cloud.** Many depend on hosted AI services, so
   recordings of patients leave the clinic.
3. **You can't check how accurate they are.** Few publish an evaluation
   that someone else can rerun and get the same numbers.
4. **Open-source attempts stop short.** The closest open project that turns
   text into structured records (Infherno) reports that local models were
   much less reliable and recommends a paid proprietary model.

**What exists today (GitHub search, 2026-11-10; star counts are snapshots)**

| Project | What it does | What it doesn't do |
|---|---|---|
| phlox (110★), scribeHC (26★), MediNotes (32★), FreeScribe, AI-Scribe | Record a visit and write a note | No standards-checked structured records; no rerunnable evaluation |
| Infherno (19★) | Text notes → structured records (FHIR) | No audio; recommends a proprietary model; agent-style multi-step design |
| AWS HealthScribe | Evidence-linked notes, a commercial cloud service | Cloud only; proprietary output format |
| Medplum, OpenEMR | Health record platforms | Not scribes; they are where Auris output could go |

Within these searches we found no open-source tool that goes from audio to
standards-checked structured records with an evaluation anyone can rerun. That gap
is what Auris is for.

## 3. The question we are answering

> **Can open AI models running entirely on a laptop turn a clinical
> conversation into standards-compliant medical records that are
> traceable, honestly labelled and measurably accurate — and can we prove
> it in a way anyone can rerun?**

We do not assume the answer is yes. The plan is built so that the
evaluation can say no.

## 4. Our solution — six principles

1. **Structured records first, summary second.** The records are the
   product; the summary is rendered from them, never the other way round.
2. **Every fact has a receipt.** Each record carries the quote it came
   from, the time in the audio and who said it. A reviewer can click and
   listen.
3. **Honest labels.** Each record says exactly which checks it passed. The
   word "validated" is used only when every check, including medical
   coding, has passed. On the default laptop install no record says
   "validated": checking codes against official value sets needs a
   terminology server, which the offline default lacks, so default records
   say "profile-checked, coding not checked".
4. **Auris is built not to invent.** The extraction step cannot add codes,
   doses, dates or patient details; anything unsaid stays empty and is
   flagged. One gap remains and we measure it: speech-to-text can write
   words nobody said. That is why labels say "transcript-supported" and why
   review plays the audio.
5. **The clinician stays in charge.** Medication orders are drafts only.
   Anything risky goes to a review queue. Auris never signs or activates
   anything.
6. **Local and open.** Open-licence models and code, running offline after
   a one-time setup download.

## 5. What it involves — how Auris works

### A few terms first

| Term | Plain meaning |
|---|---|
| **FHIR** | The international standard format for exchanging health records between systems. We use version **R4**, which is what US health software supports today. |
| **US Core** | US rules on top of FHIR saying exactly which fields each record must have. We pin version **6.1.0**. |
| **Resource** | One FHIR record, such as an Observation (a measurement) or an AllergyIntolerance (an allergy). |
| **Terminology / codes** | Standard code lists so systems agree on meaning: LOINC for measurements, ICD-10-CM for diagnoses, RxNorm for drugs, SNOMED CT for clinical concepts (licensed). |
| **Validator** | The official HL7 program that checks a record against FHIR and US Core rules. |
| **ASR** | Automatic speech recognition (speech-to-text). |
| **Diarisation** | Working out "who spoke when" in a recording. |

### The journey from audio to records

1. **Record.** A visit is recorded on a single room microphone, the setup
   we test by default. A separate microphone per person makes the next
   steps easier but isn't required.
2. **Transcribe.** Speech-to-text turns the audio into text with
   timestamps.
3. **Who spoke when.** Diarisation splits the audio by voice. It only knows
   "Speaker A" and "Speaker B", not who they are.
4. **Who is who.** A separate step decides which speaker is the clinician,
   the patient, a companion or unknown. If it isn't sure, a human decides.
5. **Pull out the facts.** A local AI model reads the transcript and fills
   in a small, fixed form for each fact. For example: *allergy: penicillin;
   said by: patient; negated: no; quote: "I come out in a rash with
   penicillin"*. The model can only answer inside that form, and every fact
   must quote the transcript.
6. **Attach standard codes.** Ordinary code, not AI, looks up the official
   code for each fact (RxNorm for a drug such as amoxicillin, and so on). A
   drug family such as penicillin has no single code, so it stays as text
   and is flagged for review. The AI may only choose from the candidates
   found, or answer "none".
7. **Check against the standards.** Each record is checked in five
   separate "lanes". Auris's own checks cover the first two: form (does it
   fit our fixed form?) and evidence (does the quote really appear in the
   transcript?). The official validator covers the other three: FHIR
   rules, US Core rules and coding. Each lane reports PASS, FAIL or NOT
   CHECKED.
8. **Clinician review.** Risky items wait in a review queue: medicines,
   allergies, anything negated, and anything about someone other than the
   patient.
9. **Write the summary.** Fixed templates, not AI, write the SOAP summary
   from the accepted records.

### What Auris captures in version 1

| Said in the visit | What Auris makes |
|---|---|
| A vital sign: "BP is 128 over 82" | Observation record (blood-pressure panel, heart rate, …) |
| A diagnosis the clinician makes or confirms | Condition record (encounter diagnosis) |
| A condition the patient reports | Condition record (problem list, "unconfirmed") |
| A new medicine the clinician orders | **Draft** MedicationRequest (a proposal, never active) |
| An allergy the patient states | AllergyIntolerance record ("unconfirmed") |

Some things are kept in the summary or review list but deliberately **not**
turned into records in v1: symptoms ("cough for three days") go into the
summary as quoted text; medicines the patient is already taking go to the
review list; "no known allergies" becomes a record only if you supply a
licensed SNOMED code set and a clinician confirms it. Everything Auris
couldn't capture is shown as "discussed, not captured" so nothing silently
disappears.

## 6. Tools, stack and models — what we chose and why

Every licence below was checked against its source during review; the
exact files we download are checked again when we pin them (Appendix A11).
"MIT" and "Apache-2.0" are permissive licences that allow commercial use.

| Job | We chose | Why | Considered, not chosen (for now) |
|---|---|---|---|
| Speech-to-text engine | **faster-whisper** (MIT) | Proven, fast on ordinary CPUs, permissive | WhisperX: adds heavy dependencies for timing we may not need |
| Speech-to-text model (laptop) | **Whisper large-v3-turbo** (MIT), converted by us to a compact INT8 format | Near large-v3 quality at much lower cost; supports many languages. No official faster-whisper copy exists, so we convert OpenAI's original ourselves rather than trust an unknown copy | distil-large-v3: English-only. NVIDIA Parakeet: heavier toolkit, later |
| Speech-to-text model (server) | **Whisper large-v3** (Apache-2.0 on its Hugging Face card) | Best quality when a GPU is available | — |
| Who spoke when (default) | **Silero VAD** (MIT) + **SpeechBrain** speaker embeddings (Apache-2.0) + clustering | Needs no account or token, so it runs anywhere | — |
| Who spoke when (comparison) | **pyannote community-1** (CC-BY-4.0, requires accepting terms) | Likely more accurate; we measure whether it is worth the friction | NVIDIA Sortformer v1: non-commercial licence |
| Fact-extraction AI | **Qwen3-8B**, official GGUF build, Q4_K_M (Apache-2.0) | Fits a 16 GB laptop next to the other stages; permissive; strong at following a fixed form | Qwen3-14B/32B and gpt-oss-20b: too big for the laptop budget (on the list for servers). Llama-family medical models: custom licences |
| Running the AI | **llama.cpp** on Mac; **vLLM + XGrammar** on Linux (all permissive) | Both can force the AI's answer to match our form exactly | Ollama: works, but not our tested path |
| Medical codes | **LOINC** subset (vitals), **ICD-10-CM** (public domain), **RxNorm Current Prescribable Content** (free, no licence needed) | Free to use. Only a small LOINC vital-signs table ships with Auris, with its licence notice; everything else is downloaded at setup | SNOMED CT: needs a licence, so only if you supply your own |
| Record checking | **Official HL7 FHIR Validator** (Apache-2.0) on **Java 21 (Temurin)** | It is the authority on FHIR and US Core rules | `fhir.resources` Python library: current releases don't support R4 4.0.1 |
| Standards | **FHIR R4 4.0.1 + US Core 6.1.0** | R4 is what US systems use; US Core 6.1.0 matches the current US data rules (USCDI v3) | FHIR R5: US Core doesn't support it |
| Summary writing | **Jinja2** templates | Deterministic: no AI can add words that weren't in the records | AI-written summary: could add unsupported claims |
| Packaging | Python via **mise/uv**; **Docker** images containing code only | Reproducible installs; models and code lists are downloaded at setup, never redistributed | — |
| Evaluation baseline | A rules pipeline we build with **medspaCy** (MIT) | Shows whether the AI actually beats simple rules | — |

**Terms in this table.** *INT8* and *Q4_K_M*: ways of storing a model's
numbers more compactly so it fits in laptop memory, at a small cost in
accuracy. *GGUF*: the file format llama.cpp uses for such compact models.
*VAD* (voice activity detection): finds the parts of the audio where
someone is speaking. *XGrammar*: a library that forces the AI's output to
follow our fixed form. *USCDI*: the US government's list of health data
that systems must be able to exchange.

**Reference machines.** Laptop: Apple M2 with 16 GB of memory, the whole
pipeline under 12 GB, at most 20 minutes to process 10 minutes of audio
(to be measured and published). Server: a 24 GB-memory GPU with 64 GB of
RAM.

## 7. What the final product looks like

**One command for a first look.** On a laptop, after a one-time setup
download, a single command processes the bundled sample visit (a
fictional, professionally recorded consultation) and writes three files:

| File | What it is |
|---|---|
| `bundle.json` | The FHIR records, in the standard format health record systems accept (v1 proves this by loading them into a local test server) |
| `validation.json` | Which checks each record passed, failed or skipped |
| `note.md` | The readable SOAP summary |

**An illustrative summary** (a fictional example of the format, not real
output):

```
S — Subjective (quoted from the transcript; not FHIR records in v1)
  • Cough for three days                     [02:14 · patient]
  • Denies fever                             [02:31 · patient]
O — Objective
  • Blood pressure 128/82 mmHg               [04:10 · clinician]
      profile-checked · coding not checked
A — Assessment
  • Acute bronchitis (provisional)           [06:02 · clinician]
      profile-checked · coding not checked
P — Plan
  • DRAFT — amoxicillin 500 mg three times daily   [06:40 · clinician]
      ⚠ needs clinician review · not validated
Needs review — not validated
  • Allergy: "rash with penicillin"          [01:48 · patient]
      drug family, kept as text · needs coding review
Discussed, not captured as records
  • Smoking cessation advice                 [07:20]
```

In real output every line carries its label; nothing appears unlabelled.

**A review page.** A simple local web page lists every record with its
quote. Click to hear that moment of the audio, correct a speaker or a
fact, then approve. Any edit re-runs the checks and rewrites the summary.

**Deployment.** Runs on a Mac, Linux (with or without a GPU), or Docker.
It works on air-gapped machines by importing a prepared download cache.
Nothing is sent anywhere, and telemetry is off everywhere.

**Evaluation kit.** One command reruns our published evaluation, so anyone
can check our numbers.

## 8. Rules Auris is built and tested to enforce

- Never invents a code, dose, date, patient detail or prescriber.
- Never turns a medicine *discussion* into an order; orders are drafts and
  are never activated or signed.
- Never records a denial ("not allergic to penicillin") as if it were an
  allergy.
- Never calls a record "validated" unless every check, including coding,
  passed.
- Review can resolve or dismiss a flag but can never override a failed
  check.
- Never sends audio or text off the machine; transcript text is never
  written to logs.
- Never claims clinical approval. Auris is research software, not cleared
  for clinical use, and clinician review is mandatory.

## 9. Acceptance criteria — how we know it works

**The product succeeds when:**

1. One command turns the sample recording and its fictional intake details
   into the three files on a laptop, fully offline after setup.
2. Every record carries its evidence (quote, audio time, speaker) and an
   honest label of which checks it passed.
3. A stranger can rerun our published evaluation on the public data and get
   our numbers.
4. Any stage (speech-to-text, diariser, extraction AI, validator) can be
   swapped without touching the others.

**Each build stage ("slice", described in section 11) has a measurable
finish line:**

| Stage | Done when |
|---|---|
| Slice 1 | Every record type and every expected refusal is covered by a test case; every check lane appears in the output; the offline test passes |
| Slice 2 | Speaker roles ≥ 90% correct while covering ≥ 85% of turns; using real speaker detection loses ≤ 5 points of accuracy versus perfect speaker labels; every edit re-checks correctly; no unreviewed critical error |
| Slice 3 | All install paths (Mac, Linux, Docker, air-gapped) work on a clean machine; records load into a local FHIR test server without duplicates on rerun; the evaluation kit runs end to end on the development set |
| Slice 4 | The locked test sets are run once; on a clean machine our saved predictions reproduce the published numbers exactly and a full rerun lands within 1 point; every stress test, failure test and clinician walkthrough in section 11 is done and published; the release gate below is met, or the result is published as a "no" |

**Release (research preview) requires** (a) zero critical errors in any
export that no human reviewed, and (b) a published pre-review
critical-error rate, meaning how often Auris proposed such an error before
anyone looked, with a 95% upper bound per error type. Medicines, allergies
and negations always go to review, so (a) alone says little about the
model. The five critical errors are: an allergy denial flipped into an
allergy; the wrong drug or dose exported; a fact attributed to the wrong
person; an order that was never given; a ruled-out or denied condition
recorded as present. An accuracy score (F1) of 0.80 is our **target**, not
a promise.

## 10. How we evaluate

**The idea.** Two clinically trained people independently write down every
fact in each recorded visit, without seeing Auris's output. Disagreements
are settled by a third reviewer. Auris's output is then scored against
these "gold" answers.

**The data.**

| Dataset | What it is | How we use it |
|---|---|---|
| PriMock57 (CC BY 4.0) | 57 mock UK GP consultations with separate audio for doctor and patient, plus human transcripts | 17 for development, 40 kept untouched for the final audio test |
| ACI-Bench (CC BY 4.0) | 207 doctor–patient conversations as text (no public audio) | 23 for development, 60 for a text-only test |
| Private commissioned set | New recordings made for us, never published | Size is the owner's decision (10–30 proposed) |

PriMock57 was recorded over telemedicine with one clean channel per
speaker, so it has almost no measured vital signs, few dosed orders,
British drug names and only two speakers. Before splits are frozen we
count candidate facts per record type in its human transcripts and publish
the counts. The private set is scripted to fill the gaps: at least 25 each
of spoken vital signs, dosed orders with US drug names and affirmed
allergies, on a single room microphone, with a companion in a third of
visits.

**What we measure.**

- **Each stage:** speech-to-text error rate, with drug names and numbers
  counted separately; invented-phrase rate against the human transcripts;
  speaker-split accuracy; role accuracy.
- **Facts:** how many real facts Auris found (recall), how many of its
  facts were right (precision), and F1, a single 0–1 score that balances
  the two, per record
  type. A strict score also requires exact values, units and doses.
- **Checks:** pass rates for each validation lane, reported separately.
- **Review burden:** how much a clinician had to correct.
- **Where errors come from:** the same tests rerun with perfect transcripts
  and perfect speaker labels.
- **Baselines:** a simple rules system, the AI without our structure, and a
  larger AI model.

**Honesty rules.**

- Public datasets may have been seen by AI models during training, and we
  say so beside every result.
- Any result based on fewer than 25 examples is marked as low-confidence.
- PriMock57 is British, while our codes are American; results are reported
  per dataset.
- Until a clinic partner with consent is found, all results are labelled
  "mock consultation" results.
- The test sets are run once, in Slice 4. All tuning and fixing uses
  development data, so the published numbers are not tuned to the test.

**Annotation budget.** Working formula: 65 h + 2.4 h per test visit + 1.5 h
per development visit. That gives ≈ 365 annotator-hours for the full design
(100 public test visits) or ≈ 269 h for a reduced one (60: 40 PriMock57 + 20
ACI-Bench). Neither includes the private set, which adds 2.4 h per visit.
All are re-priced after a timed 5–10 visit pilot.
Annotation guidelines are frozen only when the two annotators agree well:
they find the same facts (detection F1 ≥ 0.80) and label them the same way
(kappa ≥ 0.70; kappa is a standard 0–1 measure of agreement beyond
chance). If they can't get there after three
rounds, we simplify what we annotate rather than lower the bar.

## 11. Build plan

1. **Slice 1 — prove the contract.** Generate a checklist of every required
   field for each record type, straight from the official definitions. Run
   the full pipeline on human-written transcripts with known speakers,
   then on the one commissioned sample recording. Offline testing from day
   one.
2. **Slice 2 — real speakers and review.** Freeze the dataset splits. Add
   speaker detection, roles, overlap handling and the review page.
3. **Slice 3 — deployment and evaluation kit.** Add Linux, Mac and Docker
   install paths, the air-gapped install and data-retention rules, and a
   test that the records load correctly into a local FHIR test server.
   Finish the evaluation kit and dry-run it on the development set.
4. **Slice 4 — test and evaluate (protected time).** Starts when Slice 3
   is done and is never shortened to absorb build overruns. It contains:
   - **The locked evaluation**, run once on the untouched test sets, with
     speed, memory and hardware figures published.
   - **Stress tests:** noisy rooms, long visits, a companion or interpreter
     in the room, accents, sound-alike drug names, and a hand-written set
     of tricky negation and attribution cases ("no penicillin allergy",
     "my mother has diabetes").
   - **Failure tests:** missing intake details, corrupted or silent audio,
     validator and model crashes, and reruns that must not duplicate
     records.
   - **Clinician walkthrough:** clinicians use the review page on realistic
     visits; we measure correction time and record where it misleads.
   - **Error analysis with a fix-and-retest loop on development data
     only**, so the test sets stay untouched; every fix reruns the full
     regression suite.
   - **Independent reproduction:** someone outside the build reruns the
     published evaluation on a clean machine.

## 12. Time, cost and people

**Timeline (rough estimate, not yet validated).** About 4–6 weeks for
each build slice (1–3) for one full-time engineer, plus a protected 6–8
weeks for Slice 4 (test and evaluate), so roughly 4½–6 months to the
research preview. Annotation runs in parallel with Slices 1 and 2. Each slice ends
at its finish line in section 9, not on a date; the estimate is replaced
with a measured one after Slice 1.

**People.**
- One engineer building the pipeline.
- Two clinically trained annotators (for example nurses, physicians or
  professional medical scribes) and a third clinician to settle their
  disagreements.
- A FHIR-literate reviewer for the 30-visit independent FHIR subset.
- Actors with signed voice releases for the commissioned recordings.

Who fills each role is the owner's decision.

**Money.**
- **Annotation:** cost = annotator hours × hourly rate (rate set by the
  owner). Hours are ≈ 365 or ≈ 269 (section 10), plus 2.4 h per private
  visit, plus ≈ 40 h for the 30-visit independent FHIR subset. The subset
  sits outside the formula; the ≈ 40 h figure is Fable's round-2 estimate.
- **Recordings:** Codex proposed a $3,000 allowance for actors, releases
  and studio time; to be confirmed.
- **Compute:** the laptop path needs no new hardware. A rented 24 GB GPU is
  needed only for the server profile.

**If the answer is "no".** We publish it with the same care as a "yes":
the numbers, the error analysis, the evaluation kit and the annotations
(where dataset licences allow). The pipeline remains usable as a reference
with clinician review. What we try next is chosen from the error analysis,
not guessed: a larger model on the server profile, a narrower set of
facts, or better audio capture.

## 13. Decisions needed from the owner at "go"

1. Confirm **Apache-2.0** as the licence for Auris's own code.
2. Choose the annotation budget: **≈ 365 h** (100 public test visits) or
   **≈ 269 h** (60: 40 PriMock57 + 20 ACI-Bench). Neither includes the
   private set, which adds 2.4 h per visit.
3. Choose the **private recording set** size (10–30 visits) and its budget.
4. Accept **US-only code defaults** while our development data is British.
5. Approve the **sample recording**: a fictional consultation recorded by
   actors with signed voice releases and an explicit licence, and its cost.
6. Name the **people** (annotators, adjudicator, FHIR reviewer) and the
   hourly rate.
7. Confirm the **Slice 4 time box** (6–8 weeks proposed) for testing and
   evaluation.

**Decided at "go" (2026-11-12):** item 1, Apache-2.0; item 4, US code
defaults (ICD-10-CM, RxNorm, LOINC).

---

# Appendix A — Technical contract (for implementers)

## A1. Intake contract

Intake context must supply these, never extracted or invented: Patient
(identifier system+value, name, gender), Practitioner (identifier
system+value, name.family), Encounter (status, class, type, subject,
`period.start`). These are the US Core 6.1.0 minimums. A run without them
refuses to export. The demo ships a fictional `intake.json`. Auris never
modifies the intake anchors; for example, it does not add
`Encounter.diagnosis` references to extracted Conditions in v1.

## A2. Profiles, fixed elements and validation lanes

- **Profiles.** `meta.profile` is always set; a resource without it claims
  base R4 only. The mapper injects every fixed or required element listed
  in [fhir-reference.md](fhir-reference.md): category slices, fixed
  LOINC/UCUM codes, `subject`, and `encounter` = the intake Encounter.
- **Checklist.** Slice 1 task zero generates a required/fixed-element
  checklist per claimed profile from the 6.1.0 StructureDefinitions, with
  each element's source (extracted / intake-inherited / fixed). The matrix
  below is amended if the checklist disagrees.
- **Lanes.** reduced schema → evidence and consistency → base R4 → US Core
  profile → terminology. Each reports PASS / FAIL / NOT_CHECKED.
- **How lanes are decided.** From each checker's declared scope plus issue
  code/expression, never from severity alone. A skipped or unsupported
  check is NOT_CHECKED.
- **Tiers.** Tier 0 (default): local code tables, validator terminology
  off, no network; the terminology lane is always NOT_CHECKED. Tier 1
  (opt-in): a self-hosted terminology server with user-licensed content;
  only tier 1 can report terminology PASS.
- **Mapper flags are not lanes.** Flags such as `uncoded_text_only` are
  review reasons.
- **Concept normalisation (Step 9).** `concept_text_not_in_quote` requires
  clinician review; it is not an evidence-lane FAIL. Unsupported vital/dose
  numbers and unsupported allergen text remain hard FAILs. Quote alignment,
  qualifier checks and the shown-window citation boundary remain mandatory.
- **Repair.** Validator feedback may trigger one evidence-grounded
  structural repair, never invented facts.

## A3. Export and acceptance matrix

| Assertion | Polarity | Resource / profile | Fixed semantics & mapper rules | Export rule |
|---|---|---|---|---|
| Vital sign (value+unit) | Affirmed | Observation, specific US Core vital profile | `status=preliminary`; category VSCat `vital-signs`; fixed LOINC + UCUM per profile (e.g. HR 8867-4 `/min`). BP = 85354-9 with systolic 8480-6 / diastolic 8462-4 components in `mm[Hg]`, root `value[x]` omitted. Body temperature, weight and height accept only their profile's required UCUM unit set. Pulse oximetry carries the SpO2 value only; a spoken oxygen flow rate or concentration goes to the ledger in v1. An in-visit measurement with no spoken time inherits `Encounter.period.start` with a provenance note; reported or historical readings need a spoken date at day precision (vs-1) | Export if lanes pass. Missing BP component, a unit-mismatched value, a missing spoken unit where the claimed profile does not fix exactly one unit (blood pressure and heart rate fix one, so the mapper supplies it), or a historical reading without a date → review. Never silently converted or padded with dataAbsentReason |
| Symptom | Affirmed/negated | No resource in v1 | Ledger entry with polarity; rendered under S as transcript-supported text | Review-visible; not claimed as FHIR |
| Clinician-assessed problem | Affirmed | Condition (us-core-condition-encounter-diagnosis) | `category=encounter-diagnosis`; `verificationStatus=provisional`; `clinicalStatus` from explicit temporality (current → `active`, explicitly resolved → `resolved`); onset/abatement only from explicit evidence | Only if a clinician stated or confirmed it; historical or unclear status → review |
| Clinician-ruled-out problem | Negated | Condition (encounter-diagnosis) | `verificationStatus=refuted`, `clinicalStatus` omitted | Review queue by default; export only after clinician confirmation |
| Patient-reported condition | Affirmed | Condition (us-core-condition-problems-health-concerns) | `category=problem-list-item`; `verificationStatus=unconfirmed`; `clinicalStatus` from explicit temporality | Experiencer must be the patient; historical or unclear → review; negated history stays in the ledger |
| Explicit new spoken order | Affirmed | MedicationRequest (us-core-medicationrequest) | `status=draft`, `intent=proposal`. `requester` = the identified initiating clinician, confirmed against the intake Practitioner (Auris policy; US Core requires requester only for order intents). `authoredOn` inherits `Encounter.period.start` with provenance; `reportedBoolean=false`; RxNorm or text-only medication; dose verbatim in `dosageInstruction.text` or absent | An ambiguous or negated discussion, or an ambiguous prescriber, never becomes a request. Never activated or signed |
| Reported/continuing medication | Any | No MedicationRequest in v1 | Ledger + review queue (MedicationStatement is post-v1) | Never exported as an order |
| Allergy statement | Affirmed | AllergyIntolerance (us-core-allergyintolerance) | `clinicalStatus=active` (ait-1; a historical reaction is still active risk) and `verificationStatus=unconfirmed`; `type` omitted unless stated; `reaction` only with a stated manifestation; drug allergens via RxNorm, others text-only with `uncoded_text_only`. A drug-family allergen (penicillin, sulfa) is never mapped to a single ingredient; it stays text-only with `uncoded_text_only` | Explicit statement only. Explicitly outgrown or resolved → review; exportable as `resolved` only after clinician confirmation. A clinician may approve text-only coding in review |
| No known allergies / allergy denial | Negated | AllergyIntolerance only with SNOMED 716186003, `verificationStatus=confirmed` and `clinicalStatus=active` (US Core NKA guidance; ait-1 requires clinicalStatus) | Requires user-supplied SNOMED and clinician confirmation; tier 0 → ledger + review, rendered under S as a transcript-supported denial | Never exported as `unconfirmed` |

## A4. Material assertions, review and export

- **Material assertion:** any medication, allergy, negated or refuted, or
  non-patient-experiencer assertion.
- **Review is required** for every material assertion before final export.
- **Material + unmapped or failing** ⇒ final export is blocked until review
  resolves or dismisses it. A preview is still allowed. Pertinent negatives
  routed cleanly to the ledger don't block.
- **Review cannot waive a validation FAIL.**
- **Non-material resources** that pass schema, evidence, R4 and profile
  lanes may be final-exported at tier 0, labelled "profile-checked,
  terminology NOT_CHECKED".
- **Review log.** Review actions go to a local append-only log: reviewer
  name and role as entered, time, target resource version hash and diff.
  This is attribution, not authentication.
- **Edits.** Any edit invalidates approval and validation and triggers
  revalidate-and-rerender.
- **No Provenance resource in v1.**

## A5. Extraction details

- **Model and decoding.** Thinking mode off; temperature 0 as the
  candidate, confirmed in Slice 1 against the model card's sampling
  settings, with output length capped; `n_ctx=8192`.
  Backend contract tests prove AST-level schema conformance and identical
  rejection of invalid structures on llama.cpp and vLLM+XGrammar; token
  streams need not be bit-identical.
- **Evidence.** Spans are segment id + quote, aligned by case- and
  punctuation-normalised token spans to exact character/audio offsets.
  Unaligned quotes fail the evidence lane; aligned quotes are labelled
  "transcript-supported", never "verified".
- **Chunking.** Whole encounters go in one pass when they fit with
  headroom. Otherwise use ~4,096-token segment-boundary windows with
  2-segment overlap, merged on (class, normalised concept, polarity,
  experiencer, evidence-span overlap). Conflicts go to review.
- **Extractor selection.** On the 40-encounter development set (17
  PriMock57 + 23 ACI-Bench; separate from the 40 PriMock57 test visits), a
  larger model
  replaces 8B only with ≥ 3 F1 points at equal coverage, no increase in
  critical errors, and within the hardware budget. Bootstrap CIs are
  reported; if the CI half-width is > 3 points, the default is labelled
  "provisional".

## A6. Frozen reference configuration (pinned in Slice 1)

| Item | Value |
|---|---|
| FHIR | R4 4.0.1; `hl7.fhir.us.core#6.1.0` (configurable) |
| Laptop ASR | whisper-large-v3-turbo → CTranslate2 INT8, CPU; conversion command, source revision and SHA-256 recorded |
| ASR decoding | VAD gating on; `condition_on_previous_text=False`; `word_timestamps=True` with `hallucination_silence_threshold`; model loaded from the local converted path only, never faster-whisper's built-in `turbo` alias (it downloads a third-party copy). Parameter names confirmed at pin |
| Extractor | `Qwen/Qwen3-8B-GGUF` Q4_K_M; revision, SHA-256 and chat-template hash recorded |
| Validator | Pinned `validator_cli.jar` is the reference; the `validator-wrapper` warm service is used only after a contract test shows identical OperationOutcomes |
| JVM | Temurin 21 via mise, heap ≤ 2 GB |
| Load order | ASR → diariser → unload → extractor → unload → validate |

## A7. Integration proof

- **Bundle.** A `transaction` Bundle with client-assigned ids and `PUT`
  requests for the anchors and the extracted resources, posted to a local
  R4 test server.
- **Must pass.** References resolve, and submitting twice creates no
  duplicates.
- **Reporting.** Server acceptance is reported separately from validator
  lanes.

## A8. Licensing, data and privacy

- **Auris code:** Apache-2.0 (owner confirms).
- **No vendoring.** Weights and terminology are never bundled, except the
  LOINC vitals subset with its licence notice. Docker images contain code
  and runtimes only; models, RxNorm, ICD-10-CM and FHIR packages are
  fetched at setup into a mounted cache, with export/import for air-gapped
  installs.
- **NOTICE** carries the LOINC short licence and a UCUM terms inventory.
- **Artifact manifest:** source, revision, checksum, licence, access
  conditions and upstream dataset provenance. For example, VoxCeleb-trained
  speaker embeddings are flagged as academic-dataset provenance for
  enterprise deployers.
- **Excluded weights.** Non-commercial and custom-licence weights are
  excluded from defaults. Unknown training provenance is recorded, not
  laundered by a permissive licence.
- **Licence audit** runs in CI across Python, Java, native code and models.
  Telemetry is off everywhere.
- **Privacy.** Outputs are written `0600` under a `0700` workdir; no
  transcript text appears in info logs; review-page audio links are local
  only.
- **Scope language:** research software, not cleared or approved for
  clinical use, clinician review mandatory; regulatory classification is
  deployment-specific.

## A9. Evaluation details

- **Gold annotation.** Fact-level gold plus a stratified 30-encounter
  independent-FHIR subset encoded from adjudicated facts, to isolate mapper
  bias. Out-of-scope important facts are recorded too.
- **Concept matching.** Concept equivalence uses blind adjudicated gold
  concept IDs, never the production mapper's lexicon.
- **Roles.** Speaker and role gold is adjudicated; ACI-Bench speaker tags
  are known to be swapped in places.
- **Storage.** Annotations are stored as offsets and hashes.
- **Scoring.** Hungarian matching over (resource class, experiencer,
  polarity, concept). The strict match adds evidence overlap, code
  equivalence, value + UCUM unit for vitals, and dose/route/frequency for
  medications.
- **Reporting.** Observed human agreement is published per type. Splits are
  frozen before Slice 2, seed-recorded, with IDs in the eval config.

## A10. Considered and deferred

| Item | Why deferred | Revisit when |
|---|---|---|
| "No diariser" text-only role baseline | Two-profile comparison comes first | Slice 2, if the profiles differ by < 2 F1 |
| gpt-oss-20b, Mistral Small | Over the laptop budget | Linux profile, Slice 3 |
| Parakeet second-opinion ASR on drug/allergy spans | Toolkit and licence review | Drug/allergy word errors above target in Slice 2 |
| Loudspeaker-replay noise test set | Needs room recordings | After Slice 2 |
| distil-large-v3 | English-only | If turbo misses the laptop runtime budget |
| HealthSage comparison | Model location unverified | Slice 3 positioning refresh |
| MedicationStatement, CarePlan, ServiceRequest, Provenance | Outside the v1 contract | After the v1 evaluation is published |

## A11. Risks and items to verify at pin time

- **Verify when pinning:**
  - ASR conversion and checksums
  - ECAPA checkpoint card
  - US Core transitive package redistribution for offline Docker
  - PyAV/FFmpeg build obligations
  - value-set expansions for extensible bindings.
- **Clinic data:** real clinic audio needs a consented partner. Until then,
  all accuracy claims are mock-consultation claims.

## A12. Sources

- FHIR: [fhir-reference.md](fhir-reference.md) (HL7 R4 4.0.1 and US Core STU6.1 pages, read 2026-11-10)
- Datasets: https://github.com/babylonhealth/primock57 · https://github.com/wyim/aci-bench
- Models: https://huggingface.co/openai/whisper-large-v3-turbo · https://huggingface.co/Qwen/Qwen3-8B-GGUF · https://huggingface.co/api/models/Qwen/Qwen3-8B · https://huggingface.co/api/models/pyannote/speaker-diarization-community-1 · https://huggingface.co/api/models/nvidia/diar_sortformer_4spk-v1
- Terminology: https://loinc.org/kb/license/ · https://www.nlm.nih.gov/research/umls/rxnorm/docs/rxnormfiles.html
- Software: https://github.com/SYSTRAN/faster-whisper · https://github.com/snakers4/silero-vad · https://github.com/speechbrain/speechbrain · https://github.com/pyannote/pyannote-audio · https://github.com/ggml-org/llama.cpp · https://github.com/vllm-project/vllm · https://github.com/mlc-ai/xgrammar · https://github.com/hapifhir/hapi-fhir · https://github.com/hapifhir/org.hl7.fhir.validator-wrapper · https://github.com/medspacy/medspacy · https://raw.githubusercontent.com/nazrulworld/fhir.resources/main/README.rst
- Prior art: https://github.com/j-frei/Infherno · https://github.com/bloodworks-io/phlox · https://github.com/trevorpfiz/scribeHC · https://github.com/yuki-2025/MediNotes · https://github.com/ClinicianFOCUS/FreeScribe · https://github.com/1984Doc/AI-Scribe · https://github.com/aws-samples/aws-healthscribe-demo · https://github.com/medplum/medplum · https://github.com/openemr/openemr
- Original manuscript: https://chandravikram.com/papers/07-auris.html
