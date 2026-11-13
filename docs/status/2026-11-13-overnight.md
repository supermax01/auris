# Overnight status — 2026-11-13

Branch `slice-1`, 32 commits ahead of `main`, nothing pushed. Implementer:
Codex `gpt-6.1-sol` at xhigh (Astra not used after your instruction). Claude
briefed, reviewed, re-ran every test (2,199 passing, including live model and
validator tests), walked the UIs in a browser, and committed each step.

## Built overnight

| Step | What | Verified how |
|---|---|---|
| 7a–7b | Annotation kit: clinician guidelines, private offline tool, agreement, adjudication, gold export | Browser walkthroughs; gold loaded by the harness |
| 8–8d | Four leaner output formats | Real-model runs; all rejected (below) |
| 9 | Fixes from the first real consultations (paraphrase rule, citation ids, timeouts) | Re-run on 3 PriMock57 visits × 4 models |
| 10 | Loop stop, malformed-item quarantine, model-card sampling test | Pre-registered comparison, 26 real runs |
| 11 | Hide inaudible segments; ignore "um"/stutters when matching quotes | Replay over 1,758 real facts: 0 changed spans |
| 12a–c | **The clinician review page** (PLAN Slice 2): approve, dismiss, correct a fact or speaker, re-check, re-render | Browser walkthroughs with the real validator |

## What we learned

**1. Leaner output doesn't work for these models.** Every compact JSON form
made the models stop after a few facts (Bonsai 2 27B recall 0.90 → ~0.6).
Normal JSON stays. Speed has to come from the model or the hardware.

**2. Qwen3-8B, our current default, loops on real speech.** It writes the
same fact over and over (up to 27 times in one call, sometimes flipping
"has it" / "doesn't have it") until it hits the token cap. The fix is a loop
stop: a call that repeats one fact a 4th time is ended and its window split.
Replayed on 103 stored calls it caught all 31 loops and stopped no normal
call. Result on Qwen: 3 real visits went from 32 min (one visit failing
with zero facts) to 21 min with all three complete. It is still the slowest
and loopiest model:

| Real visits (3, greedy) | Bonsai 2 27B | Bonsai 2 9B | Gemma 4 E4B | Qwen3-8B |
|---|---|---|---|---|
| Time per visit | ~4.9 min | **~1.7 min** | **~1.6 min** | ~7 min |
| Loop stops | 0 | 1 | 0 | 19 |
| Evidence failures (of ~110–180 facts) | **0** | 2 | 3 | 4 |

(Descriptive only: there is no clinician gold yet, so no accuracy claim.)

**3. The model cards say "don't use greedy", but greedy works better here.**
PLAN A5 required checking temperature 0 against each model card; that had
never been done. All four cards recommend sampling. A pre-registered test
with each card's own settings did not cure Qwen's loops, and it added
evidence failures for both Bonsai models (quotes that drop a nearby "not").
Temperature 0 stays, now recorded in PLAN A5.

**4. One bad fact no longer wipes out a visit.** A blank quote used to throw
away all 15 facts from a visit. Now the bad item is held back and flagged,
and final export stays blocked until a clinician dismisses it.

**5. Real speech needed two evidence fixes.** 5% of PriMock57 lines are
"inaudible" markers the model was citing; they are now hidden. Quotes that
differ only by "um"/"uh" or a stutter now match; negations and words like
"mm-hmm" always count. 15 failing facts recovered; no correct match changed.

## Try the review page

```sh
auris review serve --run out/demo
```

`out/demo` is a fresh fictional demo run from this morning (yesterday's
run, made before the review page existed, is in `out/demo-2026-11-12`).
`auris demo` refuses to overwrite a non-empty `out/demo`, so move it aside
before making a new one. Open the printed loopback URL. Every record shows its quote; approve,
dismiss with a reason, correct a fact or a speaker (the checks and the
official validator re-run in ~8 s), undo. The header says when final
export is ready and what is left out. A correction that puts a number in a
fact that isn't in the quote fails, and review can't override that.

## Waiting on you

1. **Annotators + budget.** The kit is ready; two clinically trained
   annotators can start the 8-visit pilot (`docs/annotation/coordinator.md`).
   Without gold we can't pick a model.
2. **Private recording set** (10–30 visits) and the **sample recording**.
   PriMock57 has ~5 vital signs and 0 dosed orders in 57 visits.
3. **Which model to focus on.** Qwen stays the default until dev gold (PLAN
   A5), but on real speech it is 4× slower than Bonsai 2 9B / Gemma and the
   only one that loops. Bonsai 2 27B had zero evidence failures.
4. **Push `slice-1` / open a PR?**

Small open items: "er" counts as a filler, so an "ER" (emergency room)
dropped from a quote would still match (the stored evidence always shows the
real transcript, so the risk is low); audio playback on the review page
comes with Slice 2 speech recognition.
