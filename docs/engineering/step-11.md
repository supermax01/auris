# Step 11 — Real-speech evidence fixes

Implemented the approved empty-segment filtering and conservative disfluency
alignment. No dependencies, model inference, network setup, FHIR rule changes,
or changes to `docs/PLAN.md`. All new utterances in tests are synthetic and
explicitly labelled fictional. No PriMock57 transcript text is copied here or
into fixtures. Auris remains Apache-2.0.

## Model-visible transcript

`allowed-segment-ids-v2` filters segments with no exact-v1 lexical token from
both the rendered transcript and the allowed-id list. Empty, whitespace-only
and punctuation-only text is hidden; filler-only speech remains visible.
`raw_text` is never substituted for `text`. Planning/splitting/overlap retain
the original segment objects and order; their algorithms are unchanged.
Token measurement uses the actual filtered prompt, so windows containing hidden
segments may fit a different number of tokens.

Every attempted call records `segment_ids` (the original window),
`allowed_segment_ids` (what was shown), and `hidden_segment_count`.
Extraction metadata records `window_prompt_version`. The wrapper instruction,
system prompts and schema bytes are unchanged. The version bump identifies the
filtering policy; it does not add text to unaffected prompts. An independent
historical renderer proves byte equality for all 38 eligible fixture/demo
transcripts across every extraction profile, with existing prompt snapshots
also passing.

Location-alias resolution considers only shown segments. A real id pointing
to tokenless text yields `cited_empty_segment`, including when that id is
excluded by the shown-window boundary. It remains a failed citation and a
failed evidence lane; it cannot be repaired by borrowing neighbouring text.
The new category is also exposed by citation diagnostics and mapper review
reasons. Existing out-of-window/nonexistent citation checks still apply.

## Evidence-v2

The original NFC/casefold/apostrophe/punctuation token policy is unchanged,
including significant numeric signs and internal numeric punctuation.
`normalised_tokens` used by consistency and merging is unchanged.

1. Try the original exact token sequence first (`exact-v1`). If it exists,
   keep its first occurrence and existing safety checks, even if unsafe;
   never try a more convenient disfluency occurrence instead.
2. Only when no exact sequence exists, normalise both sides identically:
   remove `DISFLUENCY_FILLERS`, version `fillers-v1`, containing exactly
   `um, umm, uh, uhh, uhm, er, erm, ah`; collapse adjacent equal sequences of
   1–3 tokens; then require exact contiguous sequence inclusion. The repeat
   policy scans left to right, shortest width first, restarting after a
   collapse until stable. A quote normalised to zero tokens cannot match.
3. Preserve the original token ranges represented by every collapsed copy.
   The aligned character/audio span covers the first through last matched
   original token, including intervening fillers and repeats. Leading/trailing
   fillers outside those endpoints are not added. Word timings are taken
   from the original token list with the existing approximation/fallback rules.

There are no fuzzy scores, synonym additions, content-word deletions or number
edits. Assent, denial and negation carry meaning: `mm-hmm`, `mhm`, `uh-huh`,
`yeah`, `no`, `nope` and other non-fillers survive. Because exact-v1 splits
hyphens, v2 explicitly protects the `uh huh` pair, including `uh-huh`, rather
than deleting its first token. Repeated negation keeps a copy of its cue.

Negation-context and qualifier/consistency checks still inspect original
aligned text. An additional v2 guard prevents a partially matched repeat's
expanded original span from introducing a negation cue absent from the quote.
For example, a partial synthetic match inside a repeated denial must not
escape the existing omission guard just because the expanded span contains
the intervening denial. Dropping/adding a negation cue or dropping a content
word remains a failure.

Aligned evidence serializes `alignment_method` as `exact-v1` or
`disfluency-v2`. The latter adds “matched ignoring fillers/repeats” to reviewer
receipts. Stored evidence and displayed ledger quotes use the original span.
The evidence label stays `transcript-supported`. Assertion evidence records
`checker_version`; extraction metadata records `evidence_checker_version`
(`evidence-v2`) and `disfluency_fillers_version` (`fillers-v1`). The 21 A3
golden outcomes add only these evidence metadata fields; their resources,
states, text, offsets and timing values are unchanged.

## Frozen replay estimate

Command, run from the worktree root:

```sh
.venv/bin/python -m auris.extract.evidence_replay \
  cache/primock-dev-models-v2 cache/primock-dev-step10 \
  --out out/step11-replay/final
```

The report is `out/step11-replay/final/evidence-replay.json` (gitignored,
0600 in a 0700 directory). It contains counts, ids, checker versions and
source SHA-256 values, with no quotes or transcript text. Snapshot:
`2026-11-13T11:21:35.484810+00:00`.

The replay keeps merged assertions, positions, citation failures and merge
conflicts fixed. It remaps with evidence-v1 and evidence-v2, rerunning
consistency and pre-review eligibility. Every v1 baseline is checked against
the frozen evidence-lane and export states. Recorded validator lanes are
reused only for identical resources; changed resources would require fresh
validation. Every successful v1 item's original character/audio span is
compared against v2, with drift raising an error. Failed visits contribute
no partial predictions. Historical Step 9 consultation replay remains pinned
to evidence-v1 so it does not silently incorporate this step's recoveries.

All 15 extractions in `primock-dev-models-v2` were included, across all five
model/profile combinations present. The two failed visits are Qwen full-v0
and full-v0-dry consultation09. At initial inspection the Step 10 directory
had no completed extraction. Two card-profile seed-0 runs completed while
this work proceeded and are included: consultation02 and consultation09.
Consultation14 had no extraction at the snapshot; the remaining registered
Step 10 comparison arms were absent. This is a snapshot of available saved
results, not the full Step 10 experiment.

Here “recovered” means `quote_not_found → disfluency-v2`, and “empty” means
`quote_not_found → cited_empty_segment` (still FAIL). Review/ledger columns
count transitions from `validation_failed`.

| Source/model/profile | Complete / skipped | Items | Recovered | Empty | Still quote-not-found | To needs-review | To ledger-only | Exact spans unchanged |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| models-v2 / Bonsai 27B / full-v0 | 3 / 0 | 140 | 0 | 0 | 0 | 0 | 0 | 140 |
| models-v2 / Bonsai 9B / full-v0 | 3 / 0 | 120 | 1 | 1 | 0 | 1 | 0 | 118 |
| models-v2 / Gemma E4B / full-v0 | 3 / 0 | 108 | 3 | 0 | 0 | 1 | 2 | 105 |
| models-v2 / Qwen 8B / full-v0 | 2 / 1 | 137 | 1 | 1 | 1 | 1 | 0 | 134 |
| models-v2 / Qwen 8B / full-v0-dry | 2 / 1 | 137 | 1 | 1 | 1 | 1 | 0 | 134 |
| step10 / Qwen 8B / full-v0-card / seed 0 | 2 / 0 | 94 | 1 | 0 | 0 | 1 | 0 | 93 |
| **Total** | **15 / 2** | **736** | **7** | **3** | **2** | **5** | **2** | **724** |

Across 729 assertions, evidence/consistency lane transitions are 714
PASS→PASS, 7 FAIL→PASS and 8 FAIL→FAIL. Ten evidence items change category:
seven recover and three remain failures with a more precise diagnosis.
Two recovered assertions are material and still require review. No resource
changes, consistency-result changes, previously passing-item regressions or
v1-aligned span changes occur. Review reasons change for ten assertions;
the seven recoveries remove `evidence_failed`, while the three hidden
citations add `cited_empty_segment`. No item becomes final-exportable.

The four full-v0 baseline arms contain **eight** `quote_not_found` evidence
items, five of which recover, plus one unchanged
`allergen_text_not_in_quote` consistency failure: nine failing assertions in
total. This differs from the supplied finding's count of nine
`quote_not_found` items. DRY duplicates Qwen's failures/recovery and is reported
separately, not as independent evidence. Across the complete snapshot the
three hard consistency failures remain: two unsupported-allergen cases
(Qwen full-v0 and its DRY duplicate) and one unsupported vital number
(Step 10 card consultation02). The two genuine quote mismatches are also
Qwen full-v0/DRY duplicates. This replay estimates alignment and review
eligibility, not clinical correctness, recall, or improvement from new
inference. Prompt filtering itself has no inference-based effect estimate.

## Verification and follow-ups

Offline synthetic tests cover each observed filler/repetition pattern,
normalisation on both sides, original Unicode character offsets and word
timings, repeat endpoints, assent/denial preservation, negation/context safety,
content-word and numeric rejection, exact-first preference, hidden ids and
aliases, per-call metadata, unchanged fixture/demo prompt bytes, and replay
baseline/hash/failed-visit handling. Existing frozen scoring tests are run
without modifying saved predictions or scorer versions.

```text
.venv/bin/pytest -q --disable-socket -m 'not llm and not validator' --tb=short
1877 passed, 5 deselected, 1 warning in 39.82s

.venv/bin/pytest -q --disable-socket tests/test_extract_step11.py tests/test_extract_step9.py tests/test_fhir_mapper.py tests/eval/test_runs.py --tb=short
352 passed in 20.77s

.venv/bin/ruff check
All checks passed!

.venv/bin/ruff format --check
158 files already formatted

git diff --check
(exit 0, no output)
```

The warning is the existing test proving sockets are blocked. An initial
unfiltered full-suite run reported 26 failures: 21 expected golden metadata
differences (corrected explicitly) and five environment-dependent integration
failures. Two local-model tests hit the sandbox's localhost-bind restriction
and then attempted to write their skip reports to the read-only cache; three
validator tests attempted temporary writes through that same cache symlink.
Those writes were denied. No inference ran and no cache write succeeded.
The five marked tests are excluded above; changing their cache handling is
outside Step 11. Claude should run them with a writable isolated cache when
appropriate. No Java validation is needed for the replay's unchanged resources.

No new dependency or licence obligation. No FHIR-reference disagreements.
Follow-ups: complete the remaining pre-registered Step 10 comparison outside
this step, replay any newly frozen results, and reconcile the supplied
quote-failure count with the category breakdown above.

Proposed A5 Evidence wording (not applied to `docs/PLAN.md`):

> Spans are segment id + quote. Try case- and punctuation-normalised exact
> token alignment first. If no exact sequence exists, remove only the
> versioned closed filler list and collapse immediate exact repetitions of
> 1–3 tokens on both sides, then require exact token-sequence inclusion.
> Assent, denial, negation, content words and numbers remain significant.
> Resolve the matched span to original character/audio offsets, preserving
> intervening fillers and repetitions; run qualifier and negation-context
> checks on original text. Record checker and alignment-method versions and
> identify disfluency matches to reviewers. Tokenless segments are not shown
> or allowed as citations. Unaligned or unsafe quotes fail the evidence lane;
> aligned quotes are labelled “transcript-supported”, never “verified”.

## Final replay (Claude, after the step-10 comparison finished)

`.venv/bin/python -m auris.extract.evidence_replay cache/primock-dev-models-v2 cache/primock-dev-step10 --out out/step11-replay/final`
over 39 completed runs (2 failed historical runs skipped): 1,758 assertions,
1,789 evidence items. All 1,748 items aligned under evidence-v1 keep exactly
the same span under v2. 15 items move FAIL → PASS (6 of them material, still
requiring review); 11 validation_failed → needs_review and 4 → ledger_only.
Remaining evidence failures: 11 quote_not_found, 7 omitted_negation_context,
7 cited_empty_segment, 1 citation_outside_window. No resource content changed.
