# Step 7b — agreement, adjudication, gold export and reachable editors

Implemented against PLAN §10 and A9 and the unchanged 7a guideline. The full plan,
verified FHIR reference, 7a report and existing gold/matching/statistics modules
were read. No disagreement with `docs/fhir-reference.md` was found. FHIR/export
policy, prompts, frozen configuration, dataset memberships, terminology defaults
and guideline bytes remain unchanged. No clinical annotations, measured human
agreement results or frozen-guideline claim were manufactured.

## Implementation

- `src/auris/annotation/comparison.py`: completed independent-file loading,
  encounter/identity/guideline/transcript compatibility checks, symmetric blind
  concept/evidence alignment through the existing Hungarian algorithm, and
  explicit semantic/evidence/note differences.
- `src/auris/annotation/agreement.py`: overall/per-kind detection counts and F1;
  matched-fact categorical kappa, raw agreement, A/B prevalence, exact-piece
  agreement, applicable/missing denominators, freeze gates and readable reports.
  It reuses the existing bootstrap for timing/budget uncertainty.
- `src/auris/annotation/adjudication.py`: typed, clinically attested third-person
  assignments with explicit tasks and preserved A/B originals; agreed carryover;
  reason-required accept-A, accept-B, correction, omission and role decisions;
  ordered server-owned audit history, completion timestamps, validation/replay,
  private save/resume, active timing and allowlisted adjudication handlers.
- `src/auris/annotation/export.py`: complete-adjudication-only private gold
  packages, trusted frozen membership, test-ledger guard before transcript/output
  access, exact 7a synthetic anchors, final role reconciliation, full audit/source
  preservation, code-system serialization compatibility with the existing harness,
  and a final `load_dataset` integrity check.
- `src/auris/annotation/cli.py`: `agreement`, `adjudicate`, `export-gold`, preparation
  without serving, and resumable mode selection through `serve`.
- `src/auris/annotation/server.py`: the same loopback/token/origin/CSP server can
  serve either human mode; existing blind routes remain isolated.
- `src/auris/annotation/page.html`: a sticky, internally scrollable wide side
  panel; pinned, internally scrollable narrow editor with reachable actions;
  explicit editor scroll/focus and Unicode-safe transcript selection return;
  accessible A/B comparison, reason inputs, existing-form corrections and audit
  display. Corrections require an explicit decision rather than autosaving a
  partial adjudication. Keyboard focus moves to the next pending task after a
  decision. Original independent-form autosave behavior is retained.
- `src/auris/eval/gold.py`: backward-compatible optional guideline SHA-256 and
  frozen source transcript SHA-256 fields. The latter preserves the original
  split identity when adjudicated speaker roles change the scoring transcript's
  content hash. No matching or scorer rules changed.
- `tests/annotation/test_agreement_adjudication.py`: fictional offline arithmetic,
  compatibility/refusal, workflow, HTTP handler, audit, export/scoring, ledger and
  actual inline JavaScript state regressions.
- `docs/annotation/coordinator.md`: complete pilot → agreement → adjudication →
  export instructions, denominators/gates, budget assumptions and test-lock policy.

## Decisions and boundaries

Required overall kappas follow the existing guideline exactly: **kind, polarity,
experiencer, temporality**. They must be defined and ≥0.70. Detection F1 must be
≥0.80 overall and for each observed kind; the per-kind detection guard implements
the guide's prohibition on hiding difficult kinds in aggregate results. Other
kappas and all per-kind labels are reported. Undefined kappa is never promoted to
a passing value. Cross-kind matches are allowed within one resource class for
overall detection and kind agreement; per-kind detection aligns each exact kind
independently. Polarity/experiencer agreement on matched facts is conditional and
mechanically constrained by detection, stated explicitly in both reports.

The measured visit coefficient is the mean **sum** of A/B active hours, replacing
both PLAN's dev/test assumptions. The default fixed overhead is explicitly an
assumed 65 hours for training, coordination and adjudication, configurable with
`--overhead-hours`. Paired-encounter bootstrap intervals are conditional on it;
they do not cover cross-corpus generalization, idle undercount or uncertainty in
overhead. C's timing is retained for later overhead revision. Private visits and
the independent-FHIR subset remain outside these public totals.

Every original A/B assertion is either carried forward or included in a task.
Task lists include important-fact and role conflicts, not only in-scope facts.
Every decision retains the original pair, after-state, actor, time and reason;
revisions append and reopen completion. The full history and historical evidence
are verified on replay/export. Files use atomic 0600 writes under 0700 workdirs.
The audit is append-only through this local tool, not cryptographic authentication
or externally immutable storage. Credentials remain a coordinator responsibility.

The gold package is valid for the existing integrity loader and harness scorer.
The FakeBackend test extracts canned fictional assertions, maps them through the
normal pipeline with FakeValidator and scores frozen predictions. It neither
runs an actual LLM nor claims clinical/FHIR-validation accuracy. Gold export never
invokes a model or inspects any production inference artifacts.

Integration exposed an existing representation difference: human annotations use
full code-system URIs, while `freeze_prediction` spells those systems with short
tags. The export-only adapter uses those same four known identities for codes and
code-based concept IDs, preserving exact human URI originals in assignment/audit.
Unknown namespaces and `auris:` concepts are untouched. A coded medication fixture
through FakeBackend proves code-only concept and strict code agreement. No scorer
rule/version, clinical identity or terminology lookup was changed.

Human test gold creation is allowed with the existing locked flag/ledger policy.
Creation and scoring are distinct operations; exports do not grant a score
reservation. Test inference/replay scoring still use the existing evaluation CLI
and shared one-run ledger. The integrity loader remains a low-level validation
API rather than an authorization layer, as documented for coordinators. The
shared conservative ledger means later test sessions/evaluation after annotation
need explicit rerun confirmation. No split is relabelled or regenerated.

## Validation and handoff

All checks use the existing project venv with networking disabled. The focused
suite tests perfect, partial and disjoint detection; normalized concept overlap;
class/kind/person/polarity/evidence constraints; a known kappa of 8/13, negative
kappa, missing labels and single-category undefined cases; freeze boundaries and
round-three instruction; paired timing/strata and budget totals; mismatch and
incomplete-work refusal; task completeness and tamper refusal; A/B/correct/omit
decisions and reasons; audit replay/revisions and invalid-evidence rejection;
direct HTTP handlers; gold provenance/intakes/source roles and scorer compatibility;
first/repeated test operations; and real editor scroll/focus/Unicode-return code.
The inline JavaScript syntax/state probe uses existing Node when available, with
no package installation or socket. Claude performs the actual browser walkthrough.

Dependencies added: **none**. Python/pydantic/stdlib and existing pytest tooling
only; authored code and fictional fixtures remain Apache-2.0. No dependency or
lockfile edits, runtime installation, network/setup commands, commits or pushes.

Final verification (direct project venv; networking disabled):

```text
.venv/bin/ruff check
All checks passed!

.venv/bin/ruff format --check
136 files already formatted

.venv/bin/pytest tests/annotation -q --disable-socket --tb=short
58 passed in 2.85s

.venv/bin/pytest -m 'not llm' -q --disable-socket --tb=short -rs
Validator wall-clock time — goldens + anchors: 8.064s
Validator wall-clock time — demo + display variants: 8.588s
Validator wall-clock time — broken resources: 6.670s
1434 passed, 2 deselected, 1 warning in 38.36s

git diff --check
(no output; exit 0)

Changed-file personal-path/email and whitespace scans: passed
Inline JavaScript syntax and actual editor focus/scroll/Unicode state: passed
```

The warning is the existing intentional socket-blocking test. Both real-model
checks were deselected; the cached official-validator regressions ran offline.
Additional commands were read-only file/git inspection, direct project-Python
editing/privacy probes and `ruff format`. Iterative lint/test findings were fixed
before these final checks, including a serialized-span versus model-object
comparison that falsely marked important-fact evidence as different. Branch
remains `slice-1`; no git history was changed.

Follow-ups for Claude/owner: review the diff, click through adjudication at wide
and ~744px widths with keyboard and pointer, run real independent clinical pilots,
review per-kind coverage/rare labels before freezing, replace assumed overhead
with measured training/adjudication time, and commit after review. The separate
30-encounter independently encoded FHIR subset remains outside this step.
