# Step 12b — correct facts and speakers, re-check and re-render

The local review page now edits the existing assertion schema and overlays
cited segment roles. Transcript words, quotes, evidence segment IDs, intake,
extraction and mapping files are never edited. No model inference, new
dependencies, FHIR rules or terminology defaults are introduced. PLAN and the
verified FHIR reference are unchanged.

## Clinician workflow

Start `auris review serve --run out/demo` for a completed run and enter a
fictional reviewer identity for the demo. Each assertion card offers
**Correct fact**. Its kind selector changes the available fields; classifications
can abstain and blank text becomes null, through the original assertion schema.
Vital fields include value, unit and stated time; medications include intent,
dose, route and frequency; allergies expose the details their kind supports.
Source role is controlled through the cited segment's speaker correction,
not a separate editable assertion field. Evidence is read-only.

Save to re-check. Every assertion version passes the original schema, quote
alignment, consistency checker, mapper and review rules. Every produced
resource and the three unchanged intake anchors go to `ValidatorRunner`.
Only affected clinical resources are submitted. Kind changes preserve the
card/resource identity. Original citation-window failures and merge-conflict
flags remain in force; edits do not widen the evidence boundary.

An unsupported vital or dose number remains an evidence/consistency FAIL.
The card explains that approval cannot override it and offers dismissal;
the clinician must document the correct value in their own system. An edited
concept absent from its quote gets the same review reason as model output;
it does not become a special clinician exception. Unsupported allergen text
also retains its hard FAIL.

Each quote offers clinician / patient / other speaker roles. A correction
applies only to that segment. All citing assertions are checked again using
the overlaid transcript and derived source role. Orders also depend on the
visit's complete set of clinician clusters, so they are rechecked on any
speaker correction, including corrections to segments they do not cite.
An order citing a speaker changed to patient loses its order candidate and
returns to the ledger with the prescriber and kind/role reasons.

Approvals bind to a review version containing the raw assertion, relevant
speaker context, original mapping identity and active correction receipt IDs.
Even a field change producing identical FHIR JSON invalidates approval and
validation. Manually entering an earlier value creates another checked
version. Cards identify earlier approvals as stale. Approval still binds the
underlying resource/ledger content for A3 export checks after the exact review
version has been confirmed.

**Undo fact correction** and **Undo speaker correction** append revocations
of the latest correction on their target. Replaying overlays restores the
preceding version, including its exact existing validation and receipts.
If undo creates a combination of fact and speaker overlays not previously
checked, that combination goes through a new re-check. Ordinary **Undo latest
action** continues to revoke approval/dismissal separately.

The ready header counts outstanding nonblocking review items absent from
the bundle and ledger-only items, excluding dismissals. For example:
“Ready to export — 1 item needs review and is not in the bundle; 1 is
ledger-only”. Ledger-only items never acquire FHIR resources through approval.

## Durable review layer

`review-versions.json` is a separate 0600 file in the private 0700 run
directory. It contains corrected assertions, segment-role payloads, mapped
versions, pending/completed checks and their typed validator results. Reads
reject symlinks and shared permissions. Writes use the existing private
temporary-file, flush/fsync and atomic-replacement primitive. The mapper's
original wire format is unchanged; version identity is a review-only model.

`review.jsonl` remains append-only. Correction events record reviewer, role,
UTC time, target, old/new SHA-256 versions and field-level before/after diffs.
Speaker events additionally list old/new versions of every affected assertion.
Revocations name their original event. Approval and dismissal receipts remain
compatible with step 12a and the original quarantine receipts.

The overlay file is written before the event that activates it. An overlay
whose event append fails is ignored. The event is durable before projection;
reopening rebuilds `note.md`, `bundle.json` and `validation.json` from sources,
the layer and the log. Pending versions carry NOT_CHECKED validator lanes
before Java starts, and the collection is reduced to anchors while blocked.
Output downloads rebuild projections before reading them, so an earlier
projection I/O failure cannot serve a stale final bundle. Replacement is
atomic per file, not a filesystem transaction across all projections.

The validation report includes effective lanes, exact review version hashes,
stale receipts, per-item check status/messages and per-version validator
metadata. The private layer retains the complete checker results. Unchanged
anchor results can be reused across transport failures; completed anchor
checks replace older results. A successful re-check can therefore recover
anchors that were originally unchecked.

## Responsive re-checks and local setup

The authenticated correction request stores and renders the pending version,
then returns HTTP 202. A single background worker runs the official validator
outside the session lock. The page polls while the worker is active, marks
affected cards **Re-checking**, and blocks final export. Unaffected approvals
and dismissals remain available; transcript reading and downloads of the
anchors-only collection remain available. Polling preserves dismissal drafts
and avoids replacing the page repeatedly while a check is still running.
Further corrections wait for that worker to finish.

Backend failure, timeout, exception or incomplete/mismatched validator results
produce NOT_CHECKED lanes and a blocked item, never an old PASS. Genuine
OperationOutcome conformance errors retain FAIL. **Retry checks** repeats
validation of that exact version. A process interruption leaves a durable
pending version that can be retried, corrected, undone or dismissed after
reopening. Approval cannot complete a pending/failed re-check.

Re-checks use the existing pinned setup artifacts and offline flags, including
`-tx n/a` and `-no-http-access`. The terminology index is read-only. Review
supplies `ValidatorRunner.work_directory` under the run directory and copies
the seeded package home there for each invocation. Java's package metadata,
temporary files and clinical inputs stay in that disposable private workdir;
the shared cache is read only. Other runner callers keep their prior behavior.

The retained step-12a demo snapshots available in this worktree record
`wall_seconds=0.0` because their structural validator was **simulated**. They
provide no real JVM latency measurement. This step therefore uses background
validation rather than assuming each check takes seconds. The existing
official runner deadline is 180 seconds. Claude's validator-backed integration
and browser walkthrough should record real elapsed times separately.

## Offline verification and walkthrough

```sh
.venv/bin/ruff check
.venv/bin/ruff format --check
.venv/bin/pytest --disable-socket -q tests/test_review_edits.py tests/test_review_page.py tests/test_validator_runner.py
.venv/bin/pytest --disable-socket -q -m 'not llm and not validator'
git diff --check
```

The new tests use authored fictional assertions and the real schema/evidence/
consistency/mapper/review path with a fake validator. They cover every editable
field for every kind, kind transitions, hard-number refusal, concept review,
stale receipts even with identical FHIR, multiple corrections and undo,
direct and indirect speaker dependencies, source-role restoration, immutable
source files, retained citation boundaries/merge conflicts, append failure,
private atomic versions, pending/retry state, failure/timeout/partial results,
anchor recovery, ready-header counts and server-side edit bounds/refusals.
The existing offline DOM probe also opens the actual fact editor, changes
kind, checks its field set and verifies the ready header and review controls.
Runner tests verify the disposable package home and unchanged source cache.
Simulated PASS is never evidence of official validator conformance.

Final restricted-worktree verification:

```text
.venv/bin/ruff check
All checks passed!
.venv/bin/ruff format --check
167 files already formatted
.venv/bin/pytest --disable-socket -q tests/test_review_edits.py tests/test_review_page.py tests/test_validator_runner.py
150 passed in 3.57s
.venv/bin/pytest --disable-socket -q -m 'not llm and not validator'
2012 passed, 5 deselected, 1 warning in 31.95s
git diff --check
(exit 0, no output)
```

The warning is the existing smoke test deliberately verifying that sockets
are blocked. The five existing integration tests are left for Claude's
setup-enabled environment. No dependencies were added.

For Claude's browser walkthrough, open a freshly generated fictional demo
using the command above. Approve a card, edit a field, observe stale approval
and Re-checking, and undo to restore the receipt. Enter an unsupported number,
confirm that Approve disappears and Dismiss remains, then undo. Change an
order's cited speaker to patient and confirm that no MedicationRequest is
produced; undo and check its receipt. Exercise kind changes, phone width,
keyboard operation, retry after an unavailable validator, and the ready
header's excluded-item counts. No audio or transcript-text editing is present.
