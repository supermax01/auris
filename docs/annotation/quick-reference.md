# Auris annotation — desk reference (pilot v1)

For clinically trained annotators. Read [guidelines.md](guidelines.md) first.
Human-only, independent, blind. No system output, AI assistant or pre-annotation.

**Workflow:** open your assignment's printed local URL → confirm/correct turn
roles → select exact words in one turn → Add fact → classify → record verbatim
details → choose a clearly fitting local code or `auris:` slug → list acceptable
wordings → check critical fields → Apply/Save → Encounter done. Add more evidence
by selecting another turn and using Add selected evidence. Edit/delete your own
facts in the lists. Save before stopping. Ctrl/⌘ S saves; Tab reaches controls;
Shift + arrow keys selects transcript text after focusing a turn.

| Kind | Quick decision |
|---|---|
| Vital sign | Stated measurement; value/unit/time verbatim; unsaid stays blank. |
| Symptom | Complaint/denial without a stated diagnosis; quoted summary only. |
| Clinician assessed problem | Clinician explicitly diagnoses/confirms; affirmed. |
| Clinician ruled-out problem | Clinician explicitly excludes; negated, absent. |
| Patient reported condition | Named condition/history without clinician confirmation. |
| Explicit new order | Clinician actually authorizes a new medicine; new_order/explicit. |
| Reported medication | Continuing/historical/discussed/unclear; never a new order. |
| Allergy statement | Affirmed risk; reaction/resolution only if stated. |
| Allergy denial | Specific substance: specific; general NKA: all, allergen blank. |

**One fact:** one concept + person + polarity + time. BP pair, drug plus dosage,
and allergen plus reaction each stay together. Distinct concepts/events split.
Repeated unchanged mentions can share spans. Include negation and family words.

**Person ≠ speaker:** a companion speaking about the patient has experiencer
patient. Family history has family_member. Other is an unrelated person. Record
unresolved person/diagnostic ambiguity as important facts for adjudication.

**Time:** current / past / resolved / future / unclear. Past tense never proves
resolved. Use resolved only if explicitly ended. Never guess a date or quantity.

**Codes:** local RxNorm IN/SCD/SBD or billable ICD-10-CM only when clearly supported;
otherwise `auris:` slug. Never guess specificity or map a drug family to an
ingredient. Acceptable wordings must preserve meaning; no system-derived synonyms.

**Critical check:** correct person/polarity; drug/dose/route/frequency; order
explicit vs not_ordered; denial scope specific/all; condition present/absent.
Unrelated attributes use none. Never fill a missing dose/unit from medical knowledge.

**Important facts list:** tests, counselling, referrals, oxygen settings, pending
differentials, allergy history not obtained. “Rule out” pending ≠ ruled out.
“Not asked” ≠ NKA. Skip chatter and unanswered questions; an answer can use the
question as extra context evidence. Do not force facts into a kind.

**Pilot:** two independent annotators, 5–10 dev visits; third clinician adjudicates.
Active time: focused + visible, 60s idle cutoff; autosave/timing every 10s. Wall
time: open encounter, server running, until done. Note off-tool work, breaks and lost intervals.
Freeze only at detection F1 ≥ 0.80 and kappa ≥ 0.70; publish per-type agreement.
After 3 unsuccessful rounds narrow the schema, never lower thresholds. Keep
original work private; send unchanged files to the coordinator, never commit them.
