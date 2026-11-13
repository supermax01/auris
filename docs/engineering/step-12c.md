# Step 12c — clinician-facing polish

Review and note rendering share `render/reasons.py`. Checker/mapper producers now
use `CheckReason` string constants; serialized codes and clinical behavior stay
the same. The vocabulary also covers `ReviewReason`, `EvidenceFailure`, citation
classes/resolution and Pydantic quarantine error types. The review API supplies
this vocabulary and per-item support codes. Lane issues, evidence failures and
quarantine diagnostics use plain language, with original codes in title text.
The coverage test enumerates the producer enums/constants, not a copied list.

Validator errors and fatal issues always remain visible. Warnings and information
under passing or unchecked lanes appear in a native **Validator notes (N)**
disclosure, closed by default and keyboard operable. Failed lanes keep every
issue visible, preserving diagnostics even if a checker attaches a non-error
issue to FAIL. Lane states and approval/export rules are unchanged.

Adjudication uses one compact A/B field table per task. Common classifications
come first, then kind-specific and remaining fields, then every evidence span's
metadata and exact quoted text. Nested fields, empty/null values, notes, IDs,
coding and critical attributes are retained. A differing row has both color and
an explicit “differs” marker, with semantic row/column headers and a keyboard
focus stop for scrolling the containing pane. Unknown future
fields also render. Exact text uses Unicode code-point offsets. The table wraps
long values and hashes within the existing tablet layout; decision controls and
required-reason workflow are unchanged.

`annotate export-gold` names the supplied folder when no adjudication files exist
and directs the coordinator to `<workdir>/adjudications`, specifically
`out/annotation/adjudication/work/adjudications` in the documented workflow.
Encounter-set mismatches list sorted missing/unexpected IDs, with `none` for an
empty side. These diagnostics appear before any gold output is created. Existing
frozen-split checks and test-ledger reservations retain their original order.

Verification uses only fictional fixtures, simulated validator results and the
existing offline Node DOM probes. It covers all reason codes; visible errors and
failed lanes; closed/expanded validator notes; every serialized A/B field;
Unicode quotes; unmatched, role and out-of-scope tasks; and CLI errors for empty,
missing or parent folders plus missing/unexpected encounter sets. DOM tests do
not replace a visual tablet/keyboard walkthrough or official-validator testing.

```sh
.venv/bin/ruff check
.venv/bin/ruff format --check
.venv/bin/pytest --disable-socket -q tests/test_reason_labels.py tests/test_review_page.py tests/annotation/test_agreement_adjudication.py tests/test_review_edits.py
.venv/bin/pytest --disable-socket -q -m 'not llm and not validator'
git diff --check
```

Final results:

```text
All checks passed!
170 files already formatted
336 passed in 3.51s
2194 passed, 5 deselected, 1 warning in 35.02s
git diff --check: exit 0, no output
```

The warning is the existing smoke test deliberately checking blocked sockets.
The focused suite was rerun after adding the table's keyboard focus stop.

No dependencies were added. PLAN and the verified FHIR reference are unchanged.
Follow-up for Claude: review the comparison at approximately 744 px and exercise
native disclosures and adjudication controls using the keyboard during the
walkthrough. Integration tests requiring the pinned model/Java setup remain for
Claude's setup-enabled environment.
