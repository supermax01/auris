"""Complete plain-language coverage from the producers' closed code vocabularies."""

from typing import get_args

import pytest
from pydantic_core import ErrorType

from auris.evidence import EvidenceFailure
from auris.extract.citations import CITATION_RESOLUTION_REASON, CitationClass
from auris.reason_codes import CheckReason
from auris.render.reasons import REASON_LABELS, reason_text
from auris.review import ReviewReason


@pytest.mark.parametrize(
    "code",
    sorted(
        {
            *EvidenceFailure,
            *CheckReason,
            *ReviewReason,
            *get_args(CitationClass),
            *get_args(ErrorType),
            CITATION_RESOLUTION_REASON,
        }
    ),
)
def test_every_review_code_has_explicit_plain_language(code):
    # Looking up the dictionary directly deliberately bypasses the legacy fallback.
    label = REASON_LABELS[code]
    assert reason_text(code) == label
    assert label.strip() and " " in label
    assert "_" not in label
    assert label.casefold() != code.replace("_", " ").casefold()
