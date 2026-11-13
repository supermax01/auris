# Auris human annotation guidelines — pilot version 1

Audience: clinically trained nurses, physicians and professional medical scribes.
This is research annotation, not care documentation or clinical advice. The
coordinator supplies a pseudonym, an assignment and a private work directory.
The tool records the SHA-256 of this file with every encounter. A changed guide
requires a new assignment; never silently mix guideline versions.

## Your task

Read the assigned conversation and record every stated clinical fact in the nine
kinds below, plus important facts outside those kinds. Your answers will become
independent candidate answer keys. A third clinician resolves disagreements before
anything is called adjudicated gold. Accurate omissions, denials, attribution and
uncertainty matter as much as positive findings: a wrong answer key would reward
an unsafe system. You are judging what was said, not whether the care was correct.

Work independently. See only the transcript, this guide and the local code lookup.
Never inspect Auris predictions, notes, bundles, review queues or evaluation
reports. Never use an AI assistant, an LLM, generated summaries, pre-annotation or
a colleague's answers. The kit does not generate facts or suggest answer labels.

## What is one fact?

One fact is one clinical concept, about one person, with one polarity and time
status. Split a list of distinct symptoms, diagnoses, medicines or allergens.
A blood-pressure pair is one measurement, with both components in value text.
An allergy and its stated reaction are one fact; a reaction need not become a
second symptom unless independently described as a current complaint. A medicine
and its dose, route and frequency are one fact. Repeated wording of the same
unchanged fact can share several evidence spans; a changed dose, a different
measurement, person, polarity or time status needs a separate fact. Keep enough
context to distinguish repeated events. Flag ambiguous repetitions in notes.

## The nine kinds and their boundaries

| Tool kind | Record this | Boundary / how Auris uses it in v1 |
|---|---|---|
| `vital_sign` | A stated vital measurement, including an incomplete reading | Capture the value and unit exactly as spoken. Missing numbers stay blank. Record a spoken date/time separately. Current in-visit readings can inherit the fictional encounter date later; historical/reported readings need a spoken day to export. BP/heart-rate have a fixed export unit, but you still leave an unsaid unit blank. Other missing/mismatched units or an incomplete BP go to review. No conversion. Oxygen flow or concentration accompanying saturation belongs in the important-facts list. |
| `symptom` | A subjective complaint or its denial, without a stated diagnosis | This stays quoted text in the subjective summary, not a FHIR record. Do not diagnose from symptoms. |
| `clinician_assessed_problem` | A clinician explicitly makes or confirms a diagnosis/problem | This can become a provisional encounter Condition. A patient's belief is not clinician assessment. Past or unclear status goes to review; explicitly resolved status must actually be said. |
| `clinician_ruled_out_problem` | A clinician explicitly concludes a condition is absent or ruled out | Negated; critical condition status absent. A potential diagnosis still being investigated is not ruled out. Export is refuted, with clinician review. |
| `patient_reported_condition` | A diagnosis/history asserted by the patient or reported about someone, without clinician confirmation | Distinguish a named condition from a symptom description. For the patient this can become an unconfirmed problem-list Condition; negated history stays in the ledger. Family/other experiencers are retained for attribution and review, never made the patient's problem. |
| `explicit_new_order` | The initiating clinician clearly authorizes a new medication during this visit | Affirmed, clinician source, intent new_order, critical authorization explicit. Capture only the spoken dose/route/frequency. A later record is draft/proposal and requires review, never a signed prescription. |
| `reported_medication` | A continuing, historical, discussed or uncertain medication use/plan | Choose intent continuing, historical, discussed or unclear. Continuing an existing medicine is not a new order. A hypothetical option, recommendation to consider later, patient's request or question is not authorization. Mark critical authorization not_ordered where a discussion/use is explicitly not a new order. No MedicationRequest in v1. |
| `allergy_statement` | An affirmed statement of an allergy or intolerance risk | Name the allergen and only a stated reaction/resolution. Do not turn any adverse symptom after a drug into a claimed allergy unless stated as such. Drug families stay text, not one ingredient. A historical reaction does not mean risk resolved. Explicit outgrowth/resolution requires review. |
| `allergy_denial` | A specific denial or an explicit general report of no known allergies | Negated. Specific: named allergen and critical scope specific. General NKA: allergen blank and scope all. At default US terminology settings these stay in the ledger/review. NKA export requires user-supplied SNOMED and clinician confirmation; never select a substitute code. |

“Rule out” as an instruction to investigate is **pending**, not a negative result
and not a confirmed diagnosis. Put an unconfirmed differential, conditional
possibility or unclear diagnostic conclusion in important facts under
`pending_differential`, explain the uncertainty, and let the third clinician
review it. Do not force it into assessed or ruled-out. The reduced schema has no
separate differential kind. A clinician's own question alone is not a fact.

“Not asked” is not an allergy denial or NKA. An explicit statement that allergy
history was not obtained belongs in important facts (`allergy_history_missing`).
Silence gives no allergy fact at all. A denial of one substance is not a denial
of every allergy. A symptom denial is a symptom, not a ruled-out diagnosis.

## Polarity, person, source and time

- **Polarity:** affirmed means the statement asserts the fact; negated means it
  explicitly denies it. Negation must remain in the selected words. Do not use
  negated to mean uncertain or future. Pending uncertainty goes in important facts.
- **Experiencer:** patient, family_member or other. This is who has the condition,
  symptom, measurement or exposure, not who is talking. A companion reporting the
  patient's problem has source role companion and experiencer patient. A relative's
  own problem has experiencer family_member. Other includes an unrelated person.
  If you cannot determine the person, put the ambiguity in important facts and
  notes for adjudication; gold requires a resolved experiencer.
- **Source role:** clinician, patient, companion, other or unknown describes the
  speaker who states the fact. Confirm or correct each turn's source role in the
  page; ACI source labels can be swapped. Do not assume every printed role is
  right. Record unresolved roles as unknown with notes. A clinician-assessed or
  ruled-out fact needs clinician source; an order needs an identified clinician.
- **Temporality:** current, past, resolved, future or unclear. Use the words and
  context, never clinical assumptions. Past tense alone never establishes
  resolution. An old diagnosis may still exist. Use resolved only when explicitly
  ended/outgrown/resolved; preserve the resolution words. Future means an explicit
  planned future event. Unclear is preferable to a guess. Blank is unsaid.

## Evidence and spoken details

Select the exact words inside **one turn**, then Add fact. The page fills segment
ID, zero-based character start and exclusive end; the server reconstructs the
words and checks their UTF-8 SHA-256. Never type or repair a quote. Include the
negation, family word, uncertainty or time phrase that changes the meaning.
Select the smallest complete statement that preserves that context. If another
turn is needed, select words there and Add selected evidence to open fact.
Each span remains within its own turn. A bare answer may need the question as
additional context, but never annotate the unanswered question as a fact.

Values/units, dates, dose, route and frequency are the spoken words, not calculated
or normalized values. Do not add a default dose, interpret a tablet count as mg,
convert units, infer route from a formulation or invent a calendar date. Leave
unsaid fields blank. Concept text names the spoken clinical concept. For a BP,
keep both components together in value text. Optional canonical numeric values
and UCUM fields are for an unambiguous human transcription of stated quantities
only: no conversion or default-unit inference. Leave them blank for adjudication
if unsure. These optional fields support later record scoring; verbatim fields
remain the evidence of what was said.

## Concept IDs and acceptable wording

Use the **local** code-lookup panel when a code clearly expresses the stated
concept. Choose RxNorm ingredient or clinical drug (IN, SCD or SBD); a clinical drug
must not introduce an unstated strength or form. Choose an ICD-10-CM **billable**
code only at supported specificity. Lookup candidates are not suggestions about
what the patient has. Check the displayed meaning yourself. Codes never add
clinical facts. The tool automatically fills the selected code-system address
followed by a colon and the code. Keep that ID unchanged: this is the harness's
canonical concept identifier. No SNOMED content is supplied by default.

Otherwise enter a stable `auris:` slug, such as `auris:reported-ankle-stiffness`.
Agree on the meaning during guideline revision/adjudication, not by seeing system
outputs. Drug-family allergens must use an uncoded slug; do not select one
ingredient as a proxy. Uncertain coding should stay uncoded, with a note.

List acceptable **surface wordings**, one per line, for this same concept. Start
with the spoken wording; add only clearly equivalent spellings/abbreviations you
can justify independently. Do not broaden a symptom into a diagnosis, a drug
family into an ingredient, or an unspecified drug into a particular strength.
These human decisions define concept matching; the production mapper's lexicon
is not the answer key. Document disputed equivalences for the adjudicator.

## Critical-error fields

The scorer checks five serious errors: an allergy denial made positive; wrong
drug/dose; wrong person; an order never authorized; and a denied/ruled-out
condition made present. Therefore verify **all** of these, not just the concept:

- Every fact's polarity and experiencer, exact evidence and acceptable wording.
- Medicine identity plus spoken dose, route, frequency and intent. Explicit orders
  require authorization explicit. Use not_ordered for clearly non-order mentions;
  none means this attribute is inapplicable, not an inferred authorization.
- Allergy denial scope: none, specific or all. Every denial requires specific/all.
  Never use an affirmative allergy fact for a denial.
- Condition assertion: present for assessed/affirmed reported conditions; absent
  for ruled-out/negated reported conditions. The gold format has uncertain for
  adjudication work, but pending differentials belong in important facts during
  this pilot, so never label them present. Other kinds use none.

## Important facts outside the scope, and things to skip

Record clinically important counselling, procedures, tests, referrals, social
context, oxygen settings, pending differentials and unobtained allergy history in
the separate list. Give category, reason outside the schema, exact evidence and
notes. A referral is not a medicine order. Coverage must show what the nine kinds
miss, so do not drop these or force them into another kind.

Skip greetings, logistics without clinical content, chit-chat, repetitions that
add no new fact, and unanswered clinician questions. Do not invent an answer to a
question. If the answer states a fact elliptically, select the answer and include
the question as a separate supporting span when needed.

## Worked examples — all fictional, written only for this guide

Each blockquote below is a wholly fictional utterance, not a source encounter,
extraction example or clinical advice. Examples deliberately contain no standard
code claims. Select the entire quoted utterance where context is needed.

> The scale in this room reads sixty-four kilograms for you right now.

One vital_sign, patient, affirmed, current. Concept weight; value sixty-four;
unit kilograms. Acceptable text weight; slug `auris:body-weight`. Do not change the
verbatim value into digits; canonical values can be left for adjudication.

> At today's examination I diagnose a ganglion cyst on your wrist.

One clinician_assessed_problem, clinician source, patient, affirmed, current;
critical condition present. The diagnosis was actually stated, not inferred.

> Today's examination excludes a ganglion cyst at the wrist lump.

One clinician_ruled_out_problem, patient, negated, current; critical condition
absent. Keep excludes in evidence. No new affirmative diagnosis was stated.

> We still need imaging to rule out a ganglion cyst beside that wrist joint.

Important fact, pending_differential. It is neither diagnosed nor ruled out.

> My grandfather's cataract was diagnosed years before this appointment.

One patient_reported_condition, family_member, affirmed, past; condition present.
Keep grandfather in evidence. The past diagnosis does not establish resolution.

> Her ankles feel stiff each morning; I am speaking for the patient beside me.

Companion source, patient experiencer, symptom, affirmed, current. The speaker is
not the person with the symptom; select the full explanation.

> I am prescribing famotidine, ten milligrams by mouth at bedtime, starting this evening.

A fictional clinician's explicit authorization: explicit_new_order, new_order,
affirmed, clinician source, patient, future; authorization explicit. Concept
famotidine; dose ten milligrams; route by mouth; frequency at bedtime. Keep these
words unchanged. Choose a local code only if it does not add unstated details.
This is a fictional labelling exercise, not a treatment recommendation.

> The famotidine I already take remains ten milligrams at bedtime; no change is planned.

reported_medication, continuing, patient, affirmed, current; authorization
not_ordered. Dose ten milligrams; frequency at bedtime; route blank because it
was not said. Continuing the same medicine is not a new spoken order.

> My earlier course of famotidine ended in the spring, with no current use.

reported_medication, historical, patient, affirmed, past; authorization not_ordered.
Do not carry forward the dose from a separate fictional example.

> Famotidine is only one option to discuss after the results; I am not prescribing it today.

reported_medication, discussed, patient, affirmed as a discussion, future;
authorization not_ordered. The explicit non-authorization must remain in evidence.
No dose, route or frequency was stated. This is not an explicit_new_order.

> Kiwi fruit gives me an allergic reaction with itchy lips every time.

allergy_statement, patient, affirmed; allergen Kiwi fruit, reaction itchy lips.
Use an uncoded allergen slug. Reactions in the past do not imply resolved risk.

> Hazelnut has never caused an allergic reaction for me, although other foods have.

allergy_denial, patient, negated; allergen Hazelnut, scope specific. Include never
and the substance. Do not broaden this into NKA.

> As far as I know there are zero allergies of any sort in my history.

allergy_denial, patient, negated; allergen blank, scope all. General denial,
retained for review at default terminology settings.

> We did not obtain an allergy history during this fictional appointment.

Important fact, allergy_history_missing. This is not evidence of no allergies.

## Independent agreement, pilot and freeze

Two clinically trained annotators receive the same 5–10 development encounters
(8 by default), stratified by length, in independently shuffled orders. Read this
guide before starting; keep a separate workdir and pseudonym. Each annotator
works blind to the other and to every system output. Do not resolve disagreements
together during independent work. Record uncertainties in notes and mark each
encounter done after checking facts, roles, evidence and important facts.

Time the work. The tool reports active foreground annotation seconds with a
60-second idle cutoff, sampled every 10 seconds, and open-encounter wall seconds.
Hidden/unfocused intervals do not count as active. Wall time includes breaks while
that encounter is open, excludes time on other encounters and server downtime,
and resumes from saved totals. Completion stops timing; editing reopens it.
Closing/crashing can lose a final unsaved interval or add up to the idle cutoff;
record such gaps and off-tool training/lookup time in notes. For quiet
reading longer than a minute, interact to signal continued work; never manufacture
time to meet a budget. Use explicit Save before stopping.

Hand independent work to the coordinator. A third clinician adjudicates the
facts, person, polarity, roles, acceptable wordings and codes; retain both original
annotations. For agreement **before adjudication**, align independent facts by
concept/person/evidence using a documented blind matching rule, count matches,
A-only and B-only, and report detection F1 = 2 × matches / (A facts + B facts).
Report per-kind counts and detection F1. On the matched facts, report observed
agreement and chance-corrected Cohen's kappa for kind, polarity, experiencer and
time labels, with denominators and missing/undefined cases explicit; publish
per-kind agreement as sample size permits. Do not remove disagreements to improve
kappa, or turn undefined kappa into a passing score. The coordinator records the
matching rules and the freeze decision; this tool does not manufacture IAA.

Revise unclear rules between rounds, issue new versioned assignments, and repeat
independent annotation. Freeze only when detection F1 **≥ 0.80** and label kappa
**≥ 0.70**, with per-type agreement published. If thresholds are unmet after three
rounds, narrow the schema and repeat the pilot; **never lower the bar**. No round
is accepted solely because aggregate results hide a difficult kind.

Re-price PLAN §10's 65 hours + 2.4 hours per test visit + 1.5 hours per development
visit using timed pilot annotation, training and adjudication. The coordinator
separately includes private-set annotation and the independent FHIR subset.
People, rates and the final annotation budget remain owner decisions.

## Never do

Never look at Auris output or another independent annotator's answers. Never use
an AI assistant or LLM pre-annotation. Never guess unsaid values, units, doses,
routes, dates, diagnoses, family relationships or codes. Never infer resolution
from past tense, turn a pending rule-out into an absent condition, promote a
medication discussion into an order, or equate not asked with no allergies.
Never put real patient data, personal identities, secrets or annotations into the
public repository. Never call independent pilot work adjudicated gold.
