# Round-3 review: Gemini (Argon, thinking High, via Claude Code browser pane)

Prompt: PLAN.md + round3-claude-opus-5.5.md pasted in full, 2026-11-10.
Response recorded verbatim below (formatting normalised to Markdown).

## 1. Overall verdict on round-3 review

AMEND — Claude's review rightly catches the ait-1 allergy regression, test-split arithmetic contradictions, and missing operational pins, but several proposed amendments (A2, A3, A5, A6, A8, A9) require concrete numerical targets, US Core 6.1.0 binding alignment, or scope protection before locking.

## 2. Decisions on proposed amendments (A1–A11)

**A1 — EDIT:** "AllergyIntolerance (Affirmed): set clinicalStatus=active (http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical) and verificationStatus=unconfirmed when stated as current; if stated as past, outgrown, or resolved, route to the review queue and never auto-export as active."

**A2 — EDIT:** "Clinician-assessed problem (Affirmed, encounter-diagnosis): set clinicalStatus=active, verificationStatus=provisional. Clinician-ruled-out problem (Negated, encounter-diagnosis): set verificationStatus=refuted and omit clinicalStatus (permitted by 0..1 cardinality to avoid an invalid active + refuted state). Patient-reported condition (Affirmed, problem-list-item): map temporality current → clinicalStatus=active and past/resolved → clinicalStatus=resolved (satisfying R4 con-3); route unclear temporality to review without export; never infer abatement[x] (con-4)."

**A3 — EDIT:** "Keep validation lane states strictly PASS | FAIL | NOT_CHECKED. For a non-drug or drug-class allergen without an available code in Tier 0, emit code.text only, set mapper status UNCODED_TEXT_ONLY, and attach review reason needs_coding_review (blocking final export because allergies are material assertions); do not override the validator terminology lane state."

**A4 — EDIT:** "Freeze dataset splits at Slice 1 exit: split PriMock57 (57 audio encounters) into 17 dev / 40 held-out audio test; partition ACI-Bench into 15 dev / 40 audio-verified test / 37 text-only test, yielding 32 dev encounters and 80 held-out audio test encounters (117 total test encounters across audio + text, preserving the 375h budget; reduced 190h budget = 16 dev + 40 audio test). Scope the newly commissioned private audio set at 20 encounters as a separate budget line."

**A5 — EDIT:** "Evaluate the extractor flip criterion on the 32-encounter dev set (approximately 350–450 clinical assertions) using a paired encounter-cluster bootstrap over assertions: require at least a 25% relative reduction in critical errors or at least +3.0 micro-F1 points with the 95% CI excluding 0, within the 16 GB laptop budget. If unmet, keep Qwen3-8B as default and publish the point estimate and CI."

**A6 — EDIT:** "Pin the Slice 1 reference configuration table: Laptop ASR = Systran/faster-whisper-large-v3-turbo (int8 on CPU, float16 on Apple Silicon; verified MIT, preserving clinical token accuracy better than distil-large-v3); Extractor = Qwen/Qwen3-8B-GGUF pinned at Q4_K_M (n_ctx=8192, pinned SHA256 and chat template); Reference laptop = Apple M2/M3 with 16 GB unified memory, peak RSS ≤ 10 GB, and end-to-end runtime ≤ 90s per 10-minute audio encounter; Reference Linux GPU = 1x NVIDIA RTX 4090 (24 GB VRAM), 32 GB system RAM."

**A7 — EDIT:** "Pin the warm validator in Slice 1 to the HL7 FHIR Validator HTTP wrapper service running on Eclipse Temurin 21 via mise (bound to 127.0.0.1, pre-loaded with hl7.fhir.r4.core#4.0.1 and hl7.fhir.us.core#6.1.0 to match CLI behavior without JVM cold starts). Enforce sequential stage unloading on 16 GB laptops when RSS exceeds 10 GB. Apply chunking only when transcripts exceed 6,000 tokens, using 4,000-token windows with 500-token overlap and deterministic merge by (resource_class, normalized_concept, polarity, char_offset_overlap ≥ 0.5)."

**A8 — EDIT:** "Pin quantitative exit gates: Slice 1 exit = 100% of §3 matrix rows plus 1 refusal case passing schema, evidence, R4, and US Core 6.1.0 structure lanes in network-disabled CI. Slice 2 exit = on the 32-encounter dev set, speaker-role macro-accuracy ≥ 0.90, end-to-end assertion F1 drop vs oracle speaker roles ≤ 3.0 F1 points, and 100% edit-revalidation round-trip pass rate. Slice 3 / Release exit = clean-machine rerun reproduces published public-split F1 within ±0.5 F1 points (hardware float tolerance), in-scope assertion coverage ≥ 0.85, and 0 unadjudicated critical errors."

**A9 — EDIT:** "Non-material resources (affirmed vitals, affirmed encounter diagnoses) passing schema, evidence, R4, and profile lanes may export in Tier 0 with terminology_lane: NOT_CHECKED stamped in validation.json and note.md. Material assertions require sign-off by a reviewer configured with the clinician role; log reviewer_id, role, timestamp, action, and diff_hash in validation.json — do not emit a FHIR Provenance resource in v1."

**A10 — ACCEPT.**

**A11 — ACCEPT.**

## 3. Defects missed by the round-3 review

**M1 (Evaluation — Hungarian matching ignores vital values and medication dosages).** Matching on (resource class, experiencer, polarity, clinical concept), with strict match adding evidence-span overlap and code equivalence, would score an extracted BP of 180/110 against gold 120/80 as a true positive. Amendment: "Strict bipartite matching additionally requires numerical value + canonical UCUM unit equivalence for Observation vitals and normalized dose/route/frequency equivalence for MedicationRequest."

**M2 (US Core 6.1.0 — category slices and BP component codes).** §3 omits mandatory category slice codes (vital-signs; encounter-diagnosis or problem-list-item) and BP component codes (8480-6, 8462-4, UCUM mm[Hg]; exact slice names unverified, from memory). Amendment: "Mapper deterministically injects mandatory US Core category slices (vital-signs on Vitals; encounter-diagnosis or problem-list-item on Conditions) and binds BP components to LOINC 8480-6 and 8462-4 with valueQuantity.system='http://unitsofmeasure.org' and code='mm[Hg]'."

**M3 (Terminology — Prescribable RxNorm gaps on drug-class and non-drug allergies).** RxNorm Current Prescribable Content excludes environmental/food allergens and many class-level allergies (exact coverage unverified, from memory); with material + unmapped blocking export, Tier-0 users could never export those encounters. Amendment: "In Tier 0, clinician review may explicitly approve code.text-only export for non-drug or class allergens absent from Prescribable RxNorm, recording coding_override=clinician_approved_text in validation.json."

**M4 (Operations — duplicate anchors in transaction Bundle).** Unconditional POST of intake anchors duplicates them on repeat runs. Amendment: "Integration proof uses conditional create (POST with ifNoneExist) or PUT for intake anchors (Patient, Practitioner, Encounter) and POST with urn:uuid: fullUrls for extracted clinical resources."

## 4. Response to D2 and flawed round-3 amendments

**D2:** Claude is right on the invariant scope — con-3 applies only to problem-list-item. The round-2 rationale cited the wrong invariant for the right modeling decision: clinicalStatus is omitted on refuted encounter-diagnoses because condition-clinical has no valid state for a never-present, ruled-out condition, making active + refuted contradictory. This also reinforces keeping negated problem-list-item assertions in the ledger.

**A9 as drafted:** emitting FHIR Provenance adds a fifth resource type with US Core Provenance profile obligations (unverified, from memory) absent from §3. Keep sign-off in validation.json for v1.

**A5 as drafted:** 20+ extra double-annotated dev encounters break the budget envelopes; declaring 8B default by fiat defeats the comparison. Assertion-level evaluation with an encounter-cluster bootstrap on 32 dev encounters keeps budget and power.

**A3 as drafted:** setting the terminology lane to NOT_CHECKED for one uncoded allergen hides whether Tier-1 terminology validation ran. Reserve NOT_CHECKED for disabled terminology validation and track uncoded concepts with a mapper flag (UNCODED_TEXT_ONLY).
