# Step 6e — Bonsai comparators and long-visit extraction

Implemented on `slice-1`, 2026-11-12. Read `docs/PLAN.md` and
`docs/fhir-reference.md`; no FHIR disagreements or contract changes.

## Comparator pins

Added `bonsai-2-9b-q2_0` and `bonsai-2-27b-q2_0`, both Apache-2.0,
as comparators with the owner-supplied revisions, SHA-256 digests and byte
counts in `config/frozen.toml`. Qwen3-8B remains the frozen default.
The config records the 2026-11-04 release date (eight days old at pin time),
base models, ternary quantization, access conditions and provenance limits.

Claude supplied the Hugging Face API licence/access verification and verified
downloads. The vendor's benchmark tables are cited claims from the
[9B card](https://huggingface.co/prism-ml/Ternary-Bonsai-2-9B-gguf)
and [27B card](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf).
They are not Auris evaluation results. Claude's separate hc-01 smoke results
were approximately 68 and 25 generation tokens/s respectively, valid JSON,
and zero reasoning characters with `--reasoning off` on llama.cpp 0.9.0
build 12110. Those are single-case smoke measurements, not accuracy evidence.

Both candidates use stock-runtime `Q2_0` files and
`--reasoning off --reasoning-budget 0`. The cards specify stock llama.cpp
from b11556; Prism's `PQ2_0`/`PTQ1_0` fork-specific packs are excluded.
Inference is text-only, without mmproj. Reasoning output is a hard refusal,
including when the completion also reports `finish_reason=length`.

## Output-aware planning

Keep the original context-bounded A5 windows, then subdivide them before
generation until this provisional estimate fits the per-call output budget:

```text
planned_output_tokens = 128 + max(56 * segment_count,
                                  ceil(1.25 * rendered_transcript_tokens))
output_budget = min(max_output_tokens, n_ctx - measured_input_tokens - 256)
```

The estimate version is `primock_dev_v1`. The 128-token allowance covers output
framing. Transcript tokens include segment labels/timestamps, exclude the
fixed system prompt/schema, and use the backend tokenizer when available;
the existing conservative UTF-8 estimate is explicitly labelled otherwise.
Integer arithmetic fixes rounding. Actual generation still receives the full
available output budget, rather than capping it at the estimate.

Calibration used only the five recorded public PriMock57 **development mock
consultations**, with no transcript text copied into the repository. For
example, consultation02 generated 5,485 output tokens over 14,513 total input
tokens. That aggregate ratio (~0.38) includes repeated prompt/input and
discarded generation, so it understates the budget needed for some windows.
Successful windows of at least 50 segments reached about 51 output tokens per
segment and 1.06 output tokens per input token after subtracting the recorded
909-token system prompt (a transcript-token proxy that includes chat framing).
The 56 and 1.25 coefficients provide headroom over those broad-window maxima.
They are sizing estimates, not upper bounds or a fitted accuracy model.

Splits retain the existing deterministic balanced segment boundaries and
two-segment overlap. As in truncation recursion, three segments permit only
one overlapping segment and two split into singletons to ensure progress.
Identical resolved windows are reused. An indivisible segment exceeding the
estimate gets one capped attempt; actual singleton truncation still fails
the run. Every truncated body is discarded in full, and the existing
truncate-then-split safety net remains active.

Each call records `planned_output_tokens`, `actual_output_tokens`, raw
`transcript_tokens`, its counting method, the actual `max_tokens`, and
`output_estimate_exceeds_budget`. Existing usage, discarded-output, timing and
split metadata remain available; the estimator version/coefficients are in
decoding metadata. Missing output usage is represented as unknown, not zero.

## Assertion-local citation failures

An out-of-window or nonexistent segment id now preserves the assertion and
sets evidence FAIL with `citation_outside_window`, while schema-valid sibling
assertions and later windows continue. Failed items have no aligned span or
transcript-supported label. Mixed evidence retains the valid item's alignment
but still fails the whole assertion's evidence lane.

Window scope is checked before merging. Failed assertions remain separate
from valid overlapping copies in either order, so merging cannot erase a
failure or contaminate a valid assertion. Per-call failed positions and
post-merge positions/segment ids are recorded. Mapping propagates the failure
and review reason; review approval cannot override FAIL under A4. Existing
schema, truncation, reasoning and backend-integrity refusals remain enforced.

FakeBackend regressions reproduce a fictional consultation06-shaped sequence:
four truncations, a bad citation on call seven, and two further calls. They
cover existing-but-outside and nonexistent ids, mixed valid/invalid evidence,
valid siblings, merge isolation, serialization and refusal after approval.

## Metadata replay: projected cost, not a new model result

Reproduce without inference, network access, transcript loading or file writes:

```text
.venv/bin/python -m auris.extract.planning_replay cache/primock-dev
```

The original top-level context windows cover all segments of each of these
five visits (130, 162, 113, 104 and 139 respectively). Replay refines those
unchanged windows using recorded input counts minus the recorded system
prompt. Missing child input counts scale by segment count. Identical windows
reuse recorded output counts and truncations. For unseen windows, projected
output scales the smallest completed enclosing window by segment count,
including overlap; without a completed enclosing window it uses the planning
estimate. Unseen windows are assumed to complete without further truncation.
The JSON report labels each source separately.

| Visit (`day1_`) | Original calls | Original output tokens | Initially planned windows | Simulated calls including fallback | Simulated output tokens | Reused / estimated calls |
|---|---:|---:|---:|---:|---:|---:|
| consultation02 | 3 | 5,485 | 2 | 2 | 4,299 | 2 / 0 |
| consultation06 (failed) | 7 | 19,314 | 4 | 10 | 19,208 | 5 / 5 |
| consultation09 | 3 | 6,369 | 2 | 2 | 4,707 | 2 / 0 |
| consultation12 | 9 | 18,161 | 2 | 8 | 15,668 | 8 / 0 |
| consultation14 | 8 | 18,577 | 3 | 9 | 18,620 | 7 / 2 |

For the **four completed visits**, this projects 23 → 21 calls and
48,592 → 43,294 output tokens (5,298 fewer, **10.9%**). Consultation14 adds
one call and 43 projected tokens: conservative planning is not uniformly
cheaper. Across all five, the accounting is 30 → 31 calls and 67,906 → 62,502
tokens, but consultation06's failed original is not a completed-run baseline.
Its five estimated calls include three never reached in the original run;
their outputs and any additional truncations are unknown.

Known truncations persist: replay retains 12,288 discarded tokens each for
consultations06, 12 and 14. The original runs hit the 4,096-token ceiling even
with small windows, including ten segments. Input-size planning alone cannot
predict that behavior. Token counts for matching windows are recorded values,
but reuse still assumes identical model behavior; new chat framing, unknown
child counts, prefix-cache changes and output-density variation limit the
counterfactual. All five visits are low-confidence mock-consultation evidence.
Fresh inference is required to measure total calls, runtime and quality.

## Verification

Final checks used the project venv directly with networking disabled:

```text
.venv/bin/ruff check
All checks passed!

.venv/bin/ruff format --check
123 files already formatted

.venv/bin/pytest -m 'not llm' -q --disable-socket --tb=short -rs
Validator wall-clock time — goldens + anchors: 5.057s
Validator wall-clock time — demo + display variants: 5.034s
Validator wall-clock time — broken resources: 4.868s
1376 passed, 2 deselected, 1 warning in 19.81s

git diff --check
(no output; exit 0)
```

The warning comes from the existing intentional socket-blocking test. Two
real-model tests were deselected. Earlier targeted checks passed 154 tests;
an earlier full run found a stale replay expectation for the larger token-based
estimate, fixed before the final run. Initial checks also caught the former
whole-run citation expectation, a small-window overlap expectation, and one
formatting issue. Read-only inspection used git status/branch/diff, rg, cat,
sed, head, pwd and direct venv Python; ruff format applied formatting. The
replay command and a scalar-only Python summary reproduced the table above.

Dependencies added: **none**. The two new model candidates are Apache-2.0;
their existing cached weights were not modified. No dependency manifests or
lockfiles changed. No real-model inference, downloads or git-history changes
were performed in this step.

Deviations/open questions: none beyond the explicitly requested refinement
of A5 window sizing. Follow-ups for Claude: rerun the five development visits
and compare actual token use/runtime/coverage, particularly consultation06's
continuation and consultation14's extra planned call; evaluate Bonsai on the
development harness before any default-model decision. Investigating repetitive
small-window generation and validating the estimator across models remains
future work. Locked test sets were not used.
