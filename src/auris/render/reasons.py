"""One plain-language vocabulary for note and clinician review diagnostics."""

from typing import get_args

from pydantic_core import ErrorType

# Quarantine preserves Pydantic's error type/location, never private input or context.
# All known schema errors have a safe explanation; common form errors are more specific.
REASON_LABELS: dict[str, str] = dict.fromkeys(
    get_args(ErrorType), "An extracted field does not match the required form"
)
REASON_LABELS.update(
    {
        "medication": "Medicines require clinician review",
        "allergy": "Allergies require clinician review",
        "negated": "A denial requires review before any record export",
        "refuted": "The ruled-out problem requires clinician confirmation",
        "non_patient_experiencer": "This statement concerns someone other than the patient",
        "patient_experiencer_required": "Cannot create a patient record for another person",
        "snomed_not_supplied": (
            "The installed code set cannot represent this allergy denial as a record"
        ),
        "clinician_confirmation_required": "Clinician confirmation is required",
        "needs_coding_review": "A clinician must review the coding",
        "uncoded_text_only": "No unambiguous local code; text retained",
        "evidence_failed": "The quote could not be safely aligned to the transcript",
        "consistency_failed": "Extracted fields do not agree with the quoted evidence",
        "concept_text_not_in_quote": "The extracted concept does not occur in the quote",
        "allergen_text_not_in_quote": "The extracted allergen does not occur in the quote",
        "value_text_number_not_in_quote": (
            "An extracted measurement number does not occur in the quote"
        ),
        "dose_text_number_not_in_quote": "An extracted dose number does not occur in the quote",
        "dosage_text_not_in_quote": (
            "The dose, route or schedule could not be located verbatim in the quoted evidence"
        ),
        "merge_conflict": "Overlapping extraction windows disagree",
        "polarity_conflicts_with_quote": "The assertion conflicts with a denial in the quote",
        "negation_not_in_quote": "The extracted denial has no denial cue in the quote",
        "experiencer_cue_conflict": "The person assigned to this fact conflicts with the quote",
        "source_role_mismatch": "The extracted speaker role differs from the cited speaker",
        "kind_role_mismatch": "This kind of assertion is not supported by the cited speaker role",
        "unit_missing": "The measurement unit was not stated",
        "bp_component_missing": "One blood pressure component is missing",
        "unit_not_in_profile_set": "The measurement unit is not allowed by the claimed profile",
        "historical_without_date": "A historical reading needs an explicit date",
        "prescriber_not_identified": "The prescribing clinician could not be identified",
        "not_explicit_new_order": "Discussion does not establish a new order",
        "polarity_not_stated": "Whether this fact was affirmed or denied is unclear",
        "experiencer_unknown": "The person this statement concerns is unknown",
        "concept_text_missing": "The clinical name was not captured",
        "source_role_unknown": "The speaker's role is unknown",
        "historical_condition": "A historical or resolved condition needs clinician review",
        "condition_temporality_unclear": "The condition's current status is unclear",
        "allergy_resolution_stated": "Reported allergy resolution requires clinician confirmation",
        "temporality_unclear": "The time or current status of the statement is unclear",
        "polarity_kind_mismatch": (
            "The assertion type disagrees with whether it was affirmed or denied"
        ),
        "clinician_not_identified": (
            "A clinician has not been identified as supporting this assessment"
        ),
        "order_temporality_unclear": "It is unclear whether this is a current or future order",
        "allergen_not_stated": "The allergen was not stated",
        "terminology_release_out_of_period": "The code release does not cover the encounter date",
        "vital_not_supported": "This measurement stays in the ledger in version 1",
        "value_missing_or_unparseable": "The measurement value is missing or cannot be read safely",
        "measurement_date_not_day_precision": (
            "The measurement needs a stated date including the day"
        ),
        "vital_temporality_unclear": "The measurement time is unclear",
        "field_contains_commentary": (
            "An extracted field contains commentary instead of a stated value"
        ),
        "literal_null_normalised": "An empty-value placeholder was treated as not stated",
        "bp_parsed_from_value_and_unit": (
            "Blood pressure components were read from the value and unit fields"
        ),
        "malformed_item_quarantined": (
            "The extracted item does not fit the required form and was withheld"
        ),
        "cited_empty_segment": "The cited transcript segment contains no words",
        "citation_outside_window": (
            "The quote cites a segment outside the transcript shown to the extractor"
        ),
        "segment_not_found": "The cited transcript segment could not be found",
        "quote_has_no_tokens": "The evidence quote contains no words",
        "quote_not_found": "The evidence quote could not be found in the cited transcript segment",
        "omitted_negation_context": "The quote leaves out nearby words that express a denial",
        "in_window": "The citation points to a segment shown to the extractor",
        "adjacent_or_overlap": (
            "The citation points to a nearby segment outside the shown transcript"
        ),
        "earlier_window": "The citation points to an earlier part of the transcript",
        "malformed_or_nonexistent": (
            "The citation does not identify an available transcript segment"
        ),
        "id_format_confusion": (
            "The citation uses a time or turn label instead of a segment identifier"
        ),
        "unique_shown_location": (
            "A unique time or turn label was matched to a shown transcript segment"
        ),
        "missing": "A required field is missing",
        "extra_forbidden": "The extracted item includes a field that is not allowed",
        "union_tag_invalid": "The extracted item has an unsupported assertion kind",
        "union_tag_not_found": "The extracted item does not identify its assertion kind",
        "literal_error": "The field must use one of the allowed choices",
        "enum": "The field must use one of the allowed choices",
        "string_type": "The field must contain text",
        "string_too_short": "The field contains too little text",
        "string_too_long": "The field contains too much text",
        "list_type": "The field must contain a list of items",
        "tuple_type": "The field must contain a list of items",
        "model_type": "The extracted item must contain named fields",
        "model_attributes_type": "The extracted item must contain named fields",
        "value_error": "The extracted fields do not satisfy the required form",
        "too_short": "The extracted list contains too few items",
        "too_long": "The extracted list contains too many items",
    }
)


def reason_text(code: str) -> str:
    """Unknown historical codes still get readable text; callers retain the code."""
    return REASON_LABELS.get(code, code.replace("_", " ").capitalize())
