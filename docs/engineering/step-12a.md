# Step 12a — clinician review: view, approve, dismiss, undo

This step adds `auris review serve --run <run-directory>`. It is text-only:
there is no audio player, fact editor, speaker editor, extraction rerun, or
validation rerun. Step 12b owns edits and their revalidation.

## Run artifacts and review policy

Successful `auris run` and `auris demo` runs now retain a private
`review-inputs.json`: the supplied intake and transcript, the exact typed
validator result, and note metadata. `extraction.json` already holds mapping
outcomes, evidence offsets, ledger rows, and malformed-item diagnostics.
Opening review needs neither original input paths nor a model, terminology
index, JVM, or network. Older runs without the retained inputs are refused
with an actionable message; create a new run rather than guess their intake.

`review.jsonl` is separate from extraction. Each entry records the reviewer
name and role exactly as entered, UTC timestamp, action, target ID, target
SHA-256, reason, and an effect summary. Names and roles provide attribution,
not authentication. Resource targets use `resource_digest(resource)`, the
same digest used by mapping and validation. Ledger targets use the canonical
ledger digest; malformed targets use the existing complete quarantine-item
digest, including its transcript hash. Existing step-10 dismissal receipts
remain readable and revocable. The step-10 API and the page share the same
private append primitive (`O_APPEND | O_NOFOLLOW`, mode checks, `fsync`).

Undo appends a receipt naming the latest active action's entry ID. Replaying
revocations restores the preceding action, or the unreviewed state. It never
rewrites a receipt. Actions apply only to their exact target version. Older
hashes remain in the log and appear as stale on the page. Requests carrying
an outdated target hash are refused.

Approval is refused server-side if **any** lane is FAIL, including terminology.
Approval changes neither resource content nor any lane. Resource export still
requires current validation and PASS in schema, evidence, base R4 and profile
lanes. Tier-zero terminology remains NOT_CHECKED. A refuted Condition can
export after approval; text-only allergen coding can be approved without
inventing a code. Reported medications stay ledger entries. Allergy denials
stay ledger entries with the missing-SNOMED warning visible in Needs review,
even after their review has been confirmed. Approval cannot build a resource
that mapping refused, fill a missing field, or complete unchecked validation.
When mapping produced no resource, approval resolves the item to its unchanged
ledger entry and stops it blocking final export. The approval hash binds to
that ledger version, just as for intentional ledger-only mappings. Its label
then says ledger only and clinician-approved, without an unresolved-review
warning. An existing resource still requires all export checks; approval
cannot complete unchecked validation. An approved version offers Dismiss and
Undo only; repeated approval is refused without appending another receipt.

Every unresolved material item blocks the final collection, including reported
medications and allergy denials awaiting review. Clean pertinent-negative
symptoms and patient-reported negative history already routed to the ledger
retain the A4 exception. Once every blocker is approved or dismissed and the
intake anchors pass, the collection contains exactly eligible resources and
the unchanged intake anchors. Otherwise it contains anchors only. Dismissal
requires a reason, excludes the item from export and SOAP record sections,
and retains it in Dismissed in review. Malformed items can only be dismissed
(or their dismissal undone); they never become clinical facts.

Initial run output and every review action use the same projection code.
`note.md`, `bundle.json`, and `validation.json` are each replaced atomically
from a private temporary file, with flush/fsync before replacement. Outputs
and retained inputs are 0600 under a 0700 run directory. The extraction and
mapping artifacts are never rewritten by review. Validation retains original
lanes and review reasons, adds effective export/note state and a separate
`review` section containing attributed receipts, stale versions and allowed
actions. A durable review receipt survives a projection I/O failure; reopening
the page reloads the log and rebuilds all three projections. Replacement is
atomic per file, not a multi-file filesystem transaction.

## Local server and page

The server reuses the annotation handler's Host/Origin checks, bounded request
bodies, no-store responses, and silent HTTP logging. It binds only to
`127.0.0.1` on a random port. A random token is printed in the URL fragment;
the page reads it into memory and sends it in an authenticated request header.
All review APIs require that token, and POSTs additionally require the exact
loopback Origin. The unauthenticated HTML shell contains no visit data.

The CSP allows only hashes of the bundled inline script/style and same-origin
API connections; it excludes external resources, inline event handlers,
framing and form submission. All clinical text is inserted through
`textContent`. There are no static or arbitrary file routes. Output links
show the rewritten local paths and download the three fixed generated
documents through the authenticated output API and browser Blob URLs.

The page uses native forms, buttons, details/summary and focus indicators.
Every item has its fact, label, five lane states, plain-language reasons,
quotes and receipts. Each quote, receipt and alignment note is visible on
the card by default. Expanding Transcript context shows the cited segment
highlighted with one neighbor on either side. Untimed input uses turn receipts
without invented timestamps. Evidence-v2 alignment is identified as “matched ignoring
fillers/repeats.” At phone width, controls stack and long text wraps.

The header counts only export blockers: “Final export blocked: 1 item blocks
export” or “Final export blocked: 7 items block export”. Every blocking card,
including a quarantined item, carries a visible “blocks final export” badge.
Needs review can contain more items, including nonblocking ledger warnings.
When no blocker remains and intake checks pass, the header says “Ready to
export” and the bundle download is available. The note omits Dismissed in
review when there are no dismissals.

## Resource-less mapper audit (step 12a-fix)

Every mapper refusal below retains the complete reduced assertion in a ledger
entry with evidence. A lane-passing item can be approved into that ledger;
it remains absent from the bundle. Any FAIL lane makes it dismiss-only, with
the failed checks and reasons shown on the card. No refusal path needs an
approval exception and none can gain a resource through review.

| Path with `resource is None` and `ledger_only=False` | Why mapping refuses a resource |
|---|---|
| `Mapper._build` shared guards | Non-patient/unknown experiencer; missing or incompatible polarity; missing concept; an assessment without an identified clinician; medication discussion instead of a new order; unidentified or ambiguous prescriber (including multiple clinician clusters); unclear order temporality. |
| `vitals.build_vital` refusal | Missing/unparseable value or BP component; missing/unusable/mismatched unit; historical/reported reading without a day-precision date; invalid date; unclear measurement time. |
| `conditions._condition` defensive guard | Missing concept; normally caught first by `Mapper._build`. |
| `medications.build_order` defensive guard | Missing concept; normally caught first by `Mapper._build`. |
| `medications.build_order` instruction refusal | Dose, route or frequency text cannot be located verbatim in aligned quotes. Unsupported dose numbers additionally cause an evidence/consistency FAIL and prohibit approval. |
| `allergies.build_allergy` refusal | Allergen text missing. Unsupported allergen text additionally causes an evidence/consistency FAIL and prohibits approval. |

The other resource-less paths deliberately set `ledger_only=True`: symptoms,
reported/continuing/discussed medicines, allergy denials (including negated
allergy statements), negated patient history, and unsupported vital types.
They retain their existing A3/A4 policy. Quarantined malformed items are not
mapper outcomes and can only be dismissed or have that dismissal undone.

Run one review server per run directory. Reviewer identity is held in the
server session and entered once; restart the server to change it.

## Offline verification and fictional walkthrough

```sh
.venv/bin/ruff check
.venv/bin/ruff format --check
.venv/bin/pytest --disable-socket -q
```

`tests/test_review_page.py` uses only authored fictional fixtures and simulated
structural validator results. It covers all five FAIL guards, A3 approval
cases, unresolved/ready transitions, exact bundle membership, dismissal,
revocation, stale versions, original-source preservation, private atomic
replacements, attributed append-only logging, quarantine compatibility,
session requirements, CSP, Host/Origin/token checks, body limits, missing
file routes and silent logs. A simulated validator PASS is not evidence of
HL7 validator conformance; existing validator tests retain that role.

The fix adds duplicate-approval refusal at both service and HTTP boundaries,
resource-less approval/dismissal/undo and stale ledger hashes, the absence of
an empty dismissal section, and a full fictional replay approving all 13
items (including A2). `tests/review_page_probe.js` runs the actual inline page
script against a minimal DOM and fictional API snapshots without sockets or
browser dependencies, using an existing Node runtime when available. It checks
the seven-blocker and one-blocker header grammar, Ready to export and the bundle
link, each blocking badge, visible quotes/receipts/alignment notes, collapsed
highlighted transcript context, and the absence of Approve after approval.

For a walkthrough using a newly generated fictional model demo, run these
from the worktree after the existing setup has been completed:

```sh
.venv/bin/auris demo
.venv/bin/auris review serve --run out/demo
```

The demo command requires a new/empty `out/demo`; retain prior runs separately.
Open the exact `http://127.0.0.1:<random-port>/#token=...` URL printed by the
second command. Enter a fictional reviewer name and role. Check a visible quote
and receipt, then approve every item whose Approve action is available,
including family history (which resolves to the ledger), the refuted condition,
text-only allergy, ledger medications and allergy denial. Dismiss FAIL-lane or
malformed items with fictional reasons. The top bar should become Ready to
export. Inspect the three output links, then undo an approval to see final
export return to the blocked anchors-only collection. Check keyboard use and
a phone-width viewport. Stop with Ctrl-C.

No dependencies were added. No PLAN or verified FHIR facts were changed.

For the implementing worktree's UI-only handoff, `out/step12a-demo` has also
been prepared from the authored fictional fixture with a **simulated**
structural validator and no model inference. Its note labels that provenance.
It starts with seven review blockers and can be opened directly:

```sh
.venv/bin/auris review serve --run out/step12a-demo
```

That gitignored snapshot is for the browser walkthrough, not HL7 conformance
or extraction accuracy evidence. The full unit suite can be run in this
restricted worktree with `-m 'not llm and not validator'`; the five existing
integration tests need loopback binding and/or writable validator scratch
space, which this sandbox does not provide through the shared cache.

### Completed step-12a-fix replay

`out/step12a-fix-replay/run` is a new offline replay of the authored fictional
demo, using fixture terminology and a **simulated** structural validator, with
no model inference. Its note identifies that provenance. All 13 items were
approved, with no dismissals; A2 resolved to its unchanged ledger entry. The
same DOM probe verified “7 items block export” → “1 item blocks export” →
“Ready to export”, with the bundle link present. Mapping artifacts, original
resources and ledger entries remained unchanged. The final bundle has exactly
these ten resources (three supplied intake anchors and seven clinical records):

| Resource | Content |
|---|---|
| Patient | Supplied fictional patient (`example-patient-1`) |
| Practitioner | Supplied fictional practitioner (`example-practitioner-1`) |
| Encounter | Supplied fictional encounter (`example-encounter-1`) |
| Condition | Patient-reported asthma, unconfirmed, active |
| AllergyIntolerance | Sulfa, unconfirmed; stated reaction rash; text-only coding |
| Observation | Blood pressure 126/78 mmHg |
| Observation | Heart rate 84 /min |
| Condition | Assessed acute bronchitis, provisional, active; text-only coding |
| Condition | Ruled-out pneumonia, refuted; text-only coding |
| MedicationRequest | Draft/proposal acetaminophen; verbatim dosage instruction |

The six ledger entries, including family history, are absent from the bundle.
Exact resource IDs and facts are in `out/step12a-fix-replay/replay-report.json`.

Fix verification results:

```text
.venv/bin/ruff check
All checks passed!
.venv/bin/ruff format --check
164 files already formatted
.venv/bin/pytest --disable-socket -q tests/test_review_page.py tests/test_render.py
63 passed in 2.67s
.venv/bin/pytest --disable-socket -q -m 'not llm and not validator'
1924 passed, 5 deselected, 1 warning in 48.33s
git diff --check
(exit 0, no output)
```

The warning is the existing smoke test deliberately attempting a socket and
confirming it is blocked. The five integration tests remain for Claude to run
in the setup-enabled checkout. No dependency, PLAN or FHIR-reference changes.
