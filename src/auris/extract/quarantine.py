"""Private malformed-item diagnostics; deliberately not an assertion or fact."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from auris.reason_codes import CheckReason


class ItemError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: str
    location: tuple[str | int, ...]


class QuarantinedItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    call_index: int = Field(ge=0)
    window_id: str
    position: int = Field(ge=0)
    transcript_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    raw_item: Any
    errors: tuple[ItemError, ...]

    @property
    def id(self) -> str:
        return f"quarantine-{self.call_index:04d}-{self.position:04d}"

    def review_item(self) -> dict[str, Any]:
        # No raw model text leaves extraction.json for note/review summaries.
        return {
            "id": self.id,
            "reason": CheckReason.MALFORMED_ITEM_QUARANTINED,
            "is_material": True,
            "failing": True,
            "lanes": {"reduced_schema": {"state": "FAIL"}},
            "export_state": "validation_failed",
            "allowed_actions": ["dismiss"],
            "call_index": self.call_index,
            "window_id": self.window_id,
            "position": self.position,
        }


class QuarantineReview(BaseModel):
    """Dismissal only. There is no approval or fact-conversion operation."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    target_id: str
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: Literal["dismiss"]
    reason: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    role: str = Field(min_length=1)
    timestamp: str
