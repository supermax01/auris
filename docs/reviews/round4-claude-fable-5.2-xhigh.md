# Round-4 review: final PLAN.md — Claude Fable 5.2 (effort xhigh)

Run 2026-11-12 as a read-only subagent against the readable rewrite of
docs/PLAN.md, docs/fhir-reference.md and docs/team-brief.md. Output recorded
verbatim (one unrelated local-environment note removed; light Markdown
normalisation).

## 1. Verdict: GO WITH AMENDMENTS

The design is sound and I found no wrong FHIR rule. I re-checked requester
0..1 with us-core-21, authoredOn 0..1, the no-known-allergies guidance and
the allergy binding against the STU6.1 pages today. The amendments are text
edits, about an hour's work, but two change what you are approving: the
public audio test cannot exercise most of the product, and the release gate
as worded would pass a model that gets every allergy wrong.

## 2. Does the product make sense?

Yes, as a research instrument and reference pipeline. It is not yet a scribe
a clinician would choose: symptoms and current medicines are not records, and
the note is a templated list. §1 should say so and name the user
(researchers, health-IT developers).

- **Problem and question:** real, and the question is the right one because
  it can come back "no".
- **Differentiation:** real within open source (per-check labels, evidence
  links, rerunnable evaluation). Commercial scribes were not inspected, so
  keep "the ones we inspected".
- **Scope:** four record types, draft-only orders and local-first are the
  right size. §5's "ideally one microphone per person" is not ambient
  recording; a single room microphone should be the tested default.

## 3. Readability

§1–5, 7 and 8 work for a layperson. Five fixes:

- **§6 table:** INT8, GGUF, Q4_K_M, XGrammar, VAD and USCDI are unexplained.
- **§9–10:** "Slice" appears before §11 defines it; "kappa" and "detection
  F1" are never explained.
- **§7 sample:** only the blood-pressure line carries a check label, so
  readers will assume the rest is fully validated.
- **§8 title:** "Rules Auris never breaks" reads as a guarantee. Use "Rules
  Auris is built and tested to enforce".
- **§7 "ready to send to a health record system":** v1 only proves loading
  into a local test server (A7).

## 4. Consistency

- **§5 step 7** says the official validator runs all five lanes. Per A2 it
  runs three; form and evidence are Auris's own checks.
- **§6** says LOINC is "downloaded at setup, not bundled"; A8 bundles the
  LOINC vitals subset.
- **§5 and §7** turn "BP is 128 over 82" into a record; A3 sends any
  unitless value to review.
- **§4.3 against A2:** at tier 0 the coding lane is always NOT_CHECKED, so the
  default install never produces a "validated" record. §1–12 never say so,
  §2 sells "validated structured records", and "tier" is never defined.
- **§10 budget:** 365 h = 65 + 2.4×100 + 1.5×40, so it excludes the private
  set (24–72 h more). The 269 h option is 60 test visits; which 40 are
  dropped is unstated.
- **A5's "40-encounter dev set"** (17 + 23) collides with §10's 40 untouched
  PriMock57 test visits.
- **§6 "every licence was checked"** sits against A11's still-to-verify list,
  and Hugging Face tags `openai/whisper-large-v3` apache-2.0, not MIT.

## 5. Technical correctness

- **Evaluation coverage.** PriMock57 was recorded on "proprietary
  telemedicine software" with separate channels, averaging 9m05s
  (https://arxiv.org/pdf/2204.00333). Remote visits should mean almost no
  measured vitals. The drug names are British, and RxNorm Prescribable has
  "No drugs that are exclusively non-US"
  (https://www.nlm.nih.gov/research/umls/rxnorm/docs/prescribe.html). Most
  allergy talk will be denials, which make no record at tier 0. I expect
  under 25 examples for three of the four record types on the only audio
  test; the counts are unverified.
- **Release gate.** Three of the four critical errors fall in classes that
  always go to review (A4), so "zero unreviewed" is nearly true by
  construction. A ruled-out condition recorded as present is not listed and
  is non-material, so it can export unreviewed.
- **Speech-to-text invents.** A 2024 study found about 1% of Whisper
  transcriptions contained hallucinated phrases
  (https://arxiv.org/abs/2402.08021). Such a phrase passes the evidence lane,
  and the mitigations from my round-1 draft are no longer in the plan.
- **"Penicillin".** RxNav's exact search returns no active RxNorm concept
  (https://rxnav.nlm.nih.gov/REST/rxcui.json?name=penicillin&search=2). The
  only match, RxCUI 7986, comes from sources the Prescribable subset
  excludes; confirm against the file when pinning.
- **Decoding.** The Qwen3 card suggests temperature 0.7 for non-thinking mode
  and warns of "endless repetitions" under greedy decoding in thinking mode
  (https://huggingface.co/Qwen/Qwen3-8B). A5 fixes temperature 0 untested.
- **ASR loading.** faster-whisper's own `turbo` name resolves to
  `mobiuslabsgmbh/faster-whisper-large-v3-turbo` (`faster_whisper/utils.py`),
  the kind of third-party copy §6 says we avoid.
- **NKA row** omits `clinicalStatus`, which ait-1 requires.
- **Confirmed today:** PriMock57 and ACI-Bench CC BY 4.0; pyannote
  community-1 cc-by-4.0, gated; fhir.resources without R4 since 7.0.
  **Not re-checked:** Sortformer, LOINC, ICD-10-CM, Infherno, star counts.

## 6. Missing or over-built

- **Missing:** timeline and effort; cost in money, not only hours; who
  annotates; what happens if the answer is "no"; the sample recording as an
  owner decision (cost, voice releases, licence); whether the 30-visit FHIR
  subset is inside the 365 h.
- **Defer to ship sooner:** vLLM + XGrammar parity, the pyannote comparison,
  the warm validator service, the server profile, and exporting refuted
  Conditions and no-known-allergy records (ledger only in v1).

## 7. Top 5 changes

1. **Evaluation coverage and budget.** Add under §10's data table:
   "PriMock57 was recorded over telemedicine with one clean channel per
   speaker, so it has almost no measured vital signs, few dosed orders,
   British drug names and only two speakers. Before splits are frozen we
   count candidate facts per record type in its human transcripts and
   publish the counts. The private set is scripted to fill the gaps: at least
   25 each of spoken vital signs, dosed orders with US drug names and
   affirmed allergies, on a single room microphone, with a companion in a
   third of visits." Replace §12 item 2 with: "Choose the annotation budget:
   ≈ 365 h (100 public test visits) or ≈ 269 h (60: 40 PriMock57 + 20
   ACI-Bench). Neither includes the private set, which adds 2.4 h per visit."
   The 40 + 20 split is my proposal.

2. **Release gate.** Replace the first sentence of §9's release paragraph
   with: "Release requires (a) zero critical errors in any export that no
   human reviewed, and (b) a published pre-review critical-error rate,
   meaning how often Auris proposed such an error before anyone looked, with
   a 95% upper bound per error type. Medicines, allergies and negations
   always go to review, so (a) alone says little about the model." Add a
   fifth critical error: "a ruled-out or denied condition recorded as
   present".

3. **"Validated" and tiers.** Add to §4.3: "On the default laptop install no
   record says 'validated'. Checking codes against official value sets needs
   a terminology server, which the offline default lacks, so default records
   say 'profile-checked, coding not checked'." Add to A2: "Tier 0 (default):
   local code tables, validator terminology off, no network. Tier 1
   (opt-in): a self-hosted terminology server with user-licensed content;
   only tier 1 can report terminology PASS." In §2 change "validated
   structured records" to "standards-checked structured records".

4. **Speech-to-text invention.** Replace §4.4 with: "Auris is built not to
   invent. The extraction step cannot add codes, doses, dates or patient
   details; anything unsaid stays empty and is flagged. One gap remains and
   we measure it: speech-to-text can write words nobody said. That is why
   labels say 'transcript-supported' and why review plays the audio." Add an
   A6 row: "ASR decoding | VAD gating on; `condition_on_previous_text=False`;
   `word_timestamps=True` with `hallucination_silence_threshold`; model
   loaded from the local converted path only." (Parameter names are from
   memory.) Add to §10 "Each stage": "invented-phrase rate against the human
   transcripts".

5. **Rules the plan's own examples break.** In A3's vital row replace
   "unitless or unit-mismatched value" with: "a unit-mismatched value, or a
   missing spoken unit where the claimed profile does not fix exactly one
   unit (blood pressure and heart rate fix one, so the mapper supplies it)".
   In A3's allergy row add: "A drug-family allergen (penicillin, sulfa) is
   never mapped to a single ingredient; it stays text-only with
   `uncoded_text_only`." In §5 step 6 replace "RxNorm for penicillin" with
   "RxNorm for amoxicillin". In A5 replace "temperature 0" with "temperature
   0 as the candidate, confirmed in Slice 1 against the model card's sampling
   settings, with output length capped".
