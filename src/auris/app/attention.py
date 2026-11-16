"""Which review reasons are specific doubts, and which are routine for a kind.

Every record needs a clinician's approval (every-record-approved-v1), so a
reason that only says "medicines require review" or "a denial requires review"
is true of every such fact and flags nothing. A fact is flagged ("look
closer") only for a specific doubt: someone else, a question answered no, a
number or unit not in the words, a wrong speaker. Routine reasons that say
something about the record (no local code, a placeholder treated as empty) are
quiet notes; those that only restate the approval rule are left out. Failed
checks are shown by their lanes, not here.
"""

from __future__ import annotations

# True of every fact of its kind, and already said by "every fact needs your approval".
APPROVAL_REASONS = frozenset(
    {"negated", "refuted", "medication", "allergy", "clinician_confirmation_required"}
)
ROUTINE_REASONS = APPROVAL_REASONS | frozenset(
    {
        "needs_coding_review",
        "uncoded_text_only",
        "snomed_not_supplied",
        "literal_null_normalised",
        "bp_parsed_from_value_and_unit",
    }
)


def annotate(state: dict) -> dict:
    """Add ``attention`` (specific doubts) and ``notes`` (record notes) labels per item."""
    labels: dict[str, str] = state.get("reason_labels", {})
    attention: dict[str, list[str]] = {}
    notes: dict[str, list[str]] = {}
    for item_id, codes in state.get("reason_codes", {}).items():
        attention[item_id] = [labels.get(c, c) for c in codes if c not in ROUTINE_REASONS]
        notes[item_id] = [
            labels.get(c, c) for c in codes if c in ROUTINE_REASONS and c not in APPROVAL_REASONS
        ]
    state["attention"] = attention
    state["notes"] = notes
    state.pop("paths", None)  # Local file paths never reach the window.
    return state
