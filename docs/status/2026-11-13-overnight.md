# Overnight status — 2026-11-13

Branch `slice-1`, 24 commits ahead of `main`, nothing pushed. Implementer:
Codex `gpt-6.1-sol` at xhigh (from 7a on; Astra not used after your
instruction). Every step was reviewed, re-tested and committed by Claude.

## Built overnight

| Step | What | Verified how |
|---|---|---|
| 7a | Annotation guidelines for clinicians, a private offline annotation tool, pilot setup | Real-browser walkthrough: annotated a PriMock57 line, file private, evidence hash matched |
| 7b | Agreement scoring, adjudication mode, gold export | End to end in the browser at phone width: planted disagreement caught, reason enforced, gold loaded by the harness |
| 8–8d | Leaner output formats (4 variants) | Real-model runs; all rejected (below) |

## The two findings that matter

**1. Leaner output doesn't work for these models.** Pretty-printed JSON is
about one-third whitespace, so compact output would be ~40% fewer tokens
and 2× faster. But every compact form makes Bonsai 2 27B and Gemma stop
after the first fact:

| Format (Bonsai 2 27B, fictional check set) | Recall | s/visit |
|---|---|---|
| Normal JSON (default, kept) | 0.90 | 19 |
| Compact, instructed | 0.57 | 8 |
| Compact, enforced by grammar | 0.60 (Gemma: 0) | 8 |
| Newlines, no indentation | 0.82 | 18 |

Optional fields and shorter quotes were harmless but saved almost nothing.
Decision: keep normal JSON. Speed has to come from model choice (Bonsai 2 9B)
or GPU hardware.

**2. Real consultations (PriMock57, 3 dev visits × 4 models) exposed four
problems our fictional data never could** — now being fixed as step 9:

- A consistency rule ("the fact's name must appear in the quote") was a hard
  FAIL; real speech is paraphrased ("hypertension" for "high blood pressure"),
  so 141 items failed. → becomes a review flag.
- 84 citations pointed outside the window the model was shown on long visits.
  → investigate and fix the windowing.
- Qwen3-8B appears to fall into repetition loops at temperature 0 (13-line
  windows hitting the 4,096-token cap; one visit took 15 calls / 15 min).
  → keep discarded outputs privately for diagnosis; test a repetition-
  suppressing sampler (pre-registered).
- Bonsai 2 27B hit the 180 s HTTP timeout on a long window. → timeouts scale
  with the work.
- (My error) fictional pilot intakes used example URLs the validator rejects.

Per real 9-minute visit: Bonsai 9B ~1.5–2 min, Gemma ~1.5–2 min, Qwen ~2.5 min
(15 min when it loops), Bonsai 27B ~3–4.5 min (on your M5 Pro).

## Model picture (fictional check set — a signal, not evidence)

| | Bonsai 2 27B | Bonsai 2 9B | Gemma 4 E4B | Qwen3-8B (default) |
|---|---|---|---|---|
| Lenient F1 | **0.89** | 0.75 | 0.78 | 0.79 |
| Critical errors before review | 0 | 0 | 0 | 2 |
| Exported unreviewed | 0 | 0 | 0 | 0 |
| s/visit · peak RAM | 16.5 · 9.6 GB | **5.5 · 3.6 GB** | 6.5 · 5.3 GB | 8.1 · 6.5 GB |

Qwen stays default until real dev gold exists (PLAN A5).

## Waiting on you

1. **Annotators + budget** — the kit is ready; two clinically trained
   annotators can start the 8-visit pilot today (`docs/annotation/coordinator.md`).
2. **Private recording set** (10–30 visits) and the **sample recording** —
   PriMock57 has ~5 vital signs and 0 dosed orders in 57 visits.
3. Whether to make **Bonsai 2 27B** (accuracy) or **Bonsai 2 9B** (speed) the
   challenger to focus on once gold exists.
4. Whether to push `slice-1` / open a PR.
