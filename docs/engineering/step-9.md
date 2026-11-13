# Step 9 — fixes from PriMock57 development consultations

Implemented against PLAN A1–A8 and the verified R4 4.0.1 / US Core 6.1.0
reference. No FHIR rule, terminology content, dependency, model pin or clinical
fact was added. Source consultations remain in the ignored private cache;
this document contains counts and identifiers only. PriMock57 source data is
CC BY 4.0; Auris code and authored fictional fixtures remain Apache-2.0.

## Consistency and clinician review

`concept_text_not_in_quote` is now a consistency review reason. An aligned
quote with a normalised concept can pass the evidence lane, but the reason
forces materiality and clinician review before final export. Approval remains
bound to the resource digest. This change does not establish clinical
equivalence and does not add synonym guesses. Existing exact audited aliases
still avoid unnecessary lexical flags.

Vital-value and dose numbers absent from quotes remain non-waivable FAILs,
as does `allergen_text_not_in_quote`. Alignment, omitted-negation checks,
polarity/experiencer checks and source-role checks still apply. The Step 4
consistency documentation and PLAN A2 reflect this amendment.

## Citation investigation and fix

Read all twelve frozen extractions, validation files and sibling logs under
`cache/primock-dev-models`, using the prepared transcript hashes to verify the
source. Classify each bad citation in its originating call, before merging.
Adjacent means an existing id within two stored segment positions of the
window; overlap segments actually shown are already valid. An earlier-window
id is an existing nonadjacent id from a prior call. Unknown/unseen ids are
invalid. An exact displayed timestamp/turn label is an id-format confusion.

| Recorded category | Citation count |
| --- | ---: |
| Adjacent / omitted overlap | 0 |
| Earlier window | 0 |
| Malformed / nonexistent id | 0 |
| Id-format confusion: displayed time range used as segment_id | 84 |

| Model / visit | Time-range citations |
| --- | ---: |
| Bonsai 2 9B / day1_consultation09 | 18 |
| Bonsai 2 9B / day1_consultation14 | 14 |
| Gemma 4 E4B / day1_consultation02 | 39 |
| Gemma 4 E4B / day1_consultation09 | 13 |
| All other recorded runs | 0 |

The renderer already showed only window segments with their actual ids. The
ambiguous positional format let models copy the third bracketed field instead
of the first. Every new window now explicitly lists its allowed ids, including
overlap, identifies the first field as `segment_id`, and prohibits renumbering
or using locations as ids. Schema and system-prompt bytes remain unchanged;
metadata adds `window_prompt_version=allowed-segment-ids-v1` and the instruction
hash so changed user prompts are identifiable.

Deterministic alias resolution also handles the recorded mistake: a location
must match exactly as displayed and uniquely identify a segment shown in that
call. Ambiguous rounded timestamps, unknown locations, foreign actual ids,
earlier-window locations and unsupported quotes cannot gain evidence through
this rule. Raw responses and parsed per-call assertions retain the original
id; `citation_resolutions` records the original id, canonical id and positions.
Only resolved assertions enter the existing merge/alignment checks. No quote
search across the encounter or widening of the shown window is allowed.

## Frozen replay estimates (no inference)

Reproduce with the project venv, after the existing setup cache is present:

```sh
.venv/bin/python -m auris.extract.consultation_replay cache/primock-dev-models
```

The tool is read-only. It uses all twelve saved runs and remaps 431 merged
assertions from eleven completed runs; the failed Bonsai 27B visit's partial
calls remain diagnostic only. It verifies transcript hashes, preserves merge
conflicts, and checks that reinstating the old concept rule reproduces every
original export state. Validator lanes are carried forward only for unchanged
FHIR resources; changed resources require new validation. It does not remerge,
change gold annotations, run Java, run a tokenizer, or contact a model.

| Counterfactual | Evidence FAIL → PASS | Resulting states of affected assertions |
| --- | ---: | --- |
| Concept change alone: 141 recorded cases | 140 | 140 validation_failed → needs_review; 1 remains validation_failed because allergen text is unsupported |
| Citation fix alone: 84 recorded cases, old concept rule retained | 53 | 30 validation_failed → needs_review; 23 → ledger_only; 31 retain the old hard concept failure |
| Citation fix plus concept change: same 84 cases | 84 | 61 validation_failed → needs_review; 23 → ledger_only |

Across the completed runs, the combined changes move 201 assertions from
`validation_failed` to `needs_review` and 23 to `ledger_only`; 15 retain
validation failures. The other 192 keep their original states (116 review,
76 ledger). No assertion becomes final-exportable in this replay. These are
deterministic eligibility estimates, not correctness, recall or F1 results.
The window-instruction effect on future model output is unmeasured. The
example-URL change is not simulated by suppressing recorded validator issues.

## Explicit fictional intake metadata

`Intake.fictional` is a strict boolean, default false. `annotate pilot` now
writes true in every generated intake; the bundled example is explicitly
marked too. The extraction/run CLI passes the flag to `ValidatorRunner` as
`allow_example_urls`; evaluation accepts either explicit fictional encounter
metadata or the intake flag. Example URLs never determine fictional status.
The flag is local intake metadata and is not emitted as a FHIR element.

Unmarked or explicitly real CLI intakes retain strict validator URL checking,
even if their URLs happen to contain example.org. The tests verify both CLI
branches on otherwise identical anchors, plus strict parsing and round-trip
preservation of the flag.

Existing cached pilot intakes predate the field. Regenerate them with
`annotate pilot` or explicitly mark them fictional before new extraction runs.
Re-export affected annotation kits when their frozen intake hashes need to
change; never silently rewrite frozen inputs or infer the flag from names or
URLs. Existing frozen evaluation replays keep their original input bytes.

## Timeout budget and discarded diagnostics

Each candidate has positive finite configured throughput values: generation
20 tokens/s and prompt processing 100 tokens/s by default. These are
conservative configuration defaults, not newly measured performance. They
can be supplied per candidate without changing model pins. Each call uses:

```text
ceil(1.5 * (max_tokens / generation_tokens_per_second
            + rendered_input_tokens / prompt_tokens_per_second) + 30) seconds
```

The call records the token counts, both configured rates, multiplier, fixed
margin, source and computed seconds, and passes that timeout to the actual
HTTP request. Direct transport calls also scale, using a conservative byte
estimate when no measured-input budget was supplied. Tokenizer, template and
health requests retain their existing service timeouts.

The recorded failed Bonsai 27B 52-segment window reserved 3,460 input tokens
and 4,096 output tokens; its new configured allowance is 390 seconds instead
of 180. This does not prove the old error was a timeout: the old transport
collapsed timeout, connection and JSON failures. Future direct and wrapped
socket timeouts raise `ExtractionTimeoutError`, use a safe distinct message,
and record `failure_type=timeout`. No automatic inference retry was added.

Rejected completions, including length-limited responses with valid JSON,
are retained separately as `discarded-call-NNNN.json`, mode 0600 in the 0700
run folder. Each is labelled `status=discarded`, `used_for_assertions=false`,
and includes its rejection reason. The main extraction document stores a
reference, not the discarded body. Length-limited responses never reach the
schema parser; discarded responses never enter merging or mapping. Bodies
are held privately in memory until artifacts are written by `finish_pipeline`.

Each returned body also receives `repetition-v1` diagnostics before parsing:
the longest adjacent repeated lexical/punctuation n-gram run, considering
widths 1–16, and the ratio of repeated nonblank lines beyond first occurrences.
Both compact and pretty output are covered. No metric changes assertions or
causes retries. Calls without returned text record repetition as unknown.

## Pre-registered full-v0-dry experiment

Inspected the local `llama-server --help` and `--version`: 0.9.0, build 12110,
commit cd959bae9. DRY request parameters are supported; repeat-penalty fallback
is unnecessary for this pinned build. Parameter names and defaults were also
checked in the pinned upstream [server documentation](https://github.com/ggml-org/llama.cpp/blob/cd959bae9/tools/server/README.md).

`full-v0-dry` uses full-v0's schema, property order, system prompt, thinking-off
configuration, token ceilings and window planning, with these fixed fields:

```json
{
  "dry_multiplier": 1.0,
  "dry_base": 1.75,
  "dry_allowed_length": 2,
  "dry_penalty_last_n": 64,
  "dry_sequence_breakers": ["\n", ":", "\"", "*"],
  "samplers": ["penalties", "dry", "top_n_sigma", "top_k", "typ_p", "top_p", "min_p", "xtc", "temperature"],
  "seed": 0
}
```

The local help's base, allowed length, history size, sequence breakers and
sampler order are retained. The multiplier is set to unit strength (1.0)
solely to enable the otherwise disabled sampler; this is an a-priori choice,
not selected by visit performance. Seed 0 fixes the RNG and temperature stays
0; nonzero temperature is refused for this profile. The default chain places
DRY before greedy temperature selection. Sampler fields appear in the HTTP
payload, profile identity and decoding metadata. Full-v0 adds no sampler
overrides. There was no DRY inference or parameter search in this step.

Preregistered comparison: on development visits only, compare full-v0 and
full-v0-dry with identical model pins, schema, prompts, windows and budgets.
Report calls, truncations, output tokens, repetition metrics, wall time, and
evidence/review outcomes; report adverse changes as well as loop reductions.
Do not select parameters or make locked-test claims from these three visits.
Historical discarded Qwen bodies cannot be recovered, so greedy looping
remains a hypothesis until new diagnostic runs are available.

## Verification and follow-ups

Offline regressions cover concept review/approval, non-waivable numbers,
citation categories and alias limits through FakeBackend, fictional metadata
and strict real-intake CLI behavior, timeout arithmetic and error classes,
discarded permissions/parser exclusion, compact/pretty repetition, sampler
identity/request fields, and frozen replay state arithmetic. Existing replay
and schema/system-prompt/request-payload snapshots remain protected; frozen
request replay tests explicitly supply the original window wrapper.

Final verification commands and summary lines:

```text
.venv/bin/pytest -q --disable-socket --tb=short
1777 passed, 2 skipped, 1 warning in 49.26s

.venv/bin/pytest -q --disable-socket tests/test_extract_step9.py --tb=short
20 passed in 0.40s

.venv/bin/ruff check
All checks passed!

.venv/bin/ruff format --check
151 files already formatted

git diff --check
(exit 0, no output)
```

The warning is from the existing test proving sockets are blocked. The two
local-model smoke tests skip because the sandbox blocks localhost binding.
The offline HL7 integration tests passed using the already prepared cache.
The read-only replay command completed successfully and checked all 141
concept cases and 84 citation cases. Inspection used `llama-server --help`
and `llama-server --version`; formatting fixes used the project venv's Ruff.

No new dependencies. No git history changes, model inference, runtime or
dependency installation, or locked-test data access. Follow-ups for the
orchestrator: regenerate old pilot intakes, run the new profile comparison,
and measure throughput on target hardware if configured defaults need changing.
No disagreement with the FHIR reference was found.
