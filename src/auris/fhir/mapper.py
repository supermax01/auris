"""Deterministic A3 dispatch plus A4 review policy. No model or validator calls."""

from __future__ import annotations

from auris.assertions import (
    AllergyStatementAssertion,
    AssertionBase,
    Experiencer,
    ExplicitNewOrderAssertion,
    MedicationIntent,
    Polarity,
    Temporality,
    VitalSignAssertion,
)
from auris.consistency import check_consistency
from auris.evidence import AssertionEvidenceResult, EvidenceFailure, align_assertion_evidence
from auris.fhir.allergies import build_allergy
from auris.fhir.common import build_anchors, resource_id
from auris.fhir.conditions import (
    DIAGNOSIS,
    PROBLEM,
    build_assessed,
    build_reported_condition,
    build_ruled_out,
)
from auris.fhir.contracts import UnitSets, profile_url
from auris.fhir.ledger import build_allergy_denial, build_reported_medication, build_symptom
from auris.fhir.medications import build_order
from auris.fhir.models import BuildResult, LaneState, MappingOutcome, ValidationLanes, ledger_entry
from auris.fhir.vitals import BP_SPLIT_FIELDS_FLAG, build_vital, vital_profile, vital_synonyms
from auris.intake import Intake
from auris.review import is_material, review_reasons
from auris.terminology import TerminologyIndex
from auris.terminology.text import ICD10CM, RXNORM
from auris.transcript import SpeakerRole, Transcript


class Mapper:
    """One visit's immutable inputs, with a read-only terminology index."""

    def __init__(
        self,
        intake: Intake,
        transcript: Transcript,
        terminology: TerminologyIndex,
        unit_sets: UnitSets,
    ) -> None:
        self.intake = intake
        self.transcript = transcript
        self.terminology = terminology
        self.unit_sets = unit_sets
        self.anchors = build_anchors(intake)

    def map_assertion(
        self,
        assertion: AssertionBase,
        position: int,
        evidence: AssertionEvidenceResult | None = None,
        *,
        allowed_segment_ids: frozenset[str] | None = None,
    ) -> MappingOutcome:
        if position < 0:
            raise ValueError("assertion position must be non-negative")
        aligned = align_assertion_evidence(
            assertion, self.transcript, allowed_segment_ids=allowed_segment_ids
        )
        if evidence is not None and evidence != aligned:
            raise ValueError("evidence result does not belong to this assertion and transcript")
        identifier = resource_id(self.transcript.content_hash, position, assertion.kind)
        consistency = check_consistency(assertion, aligned, self.transcript, self._synonyms)
        assertion = consistency.usable_assertion(assertion)
        built = self._build(
            assertion, identifier, aligned, unit_unusable="unit_text" in consistency.unusable_fields
        )
        reasons = [str(reason) for reason in review_reasons(assertion)]
        reasons.extend(built.reasons)
        # This evidence-grounded field repair is informational for evaluation.
        reasons.extend(flag for flag in built.flags if flag != BP_SPLIT_FIELDS_FLAG)
        reasons.extend(consistency.review_reasons)
        if consistency.failures:
            reasons.append("consistency_failed")
        if assertion.temporality in (None, Temporality.UNCLEAR):
            reasons.append("temporality_unclear")
        if aligned.lane.value != "PASS":
            reasons.append("evidence_failed")
        if EvidenceFailure.CITATION_OUTSIDE_WINDOW in aligned.failures:
            reasons.append(EvidenceFailure.CITATION_OUTSIDE_WINDOW.value)
        cited_roles = {
            segment.role
            for item in assertion.evidence
            if (segment := self.transcript.segment_by_id(item.segment_id)) is not None
        }
        if assertion.source_speaker_role not in cited_roles:
            reasons.append("source_role_mismatch")
        unique_reasons = tuple(dict.fromkeys(reasons))
        material = is_material(assertion) or consistency.force_material
        return MappingOutcome(
            id=identifier,
            assertion_position=position,
            kind=assertion.kind,
            claimed_profile=profile_url(built.profile) if built.profile else None,
            resource=built.resource,
            ledger=ledger_entry(assertion) if built.resource is None else None,
            evidence=aligned,
            consistency=consistency,
            review_reasons=unique_reasons,
            mapper_flags=(*built.flags, *consistency.informational_flags),
            provenance_notes=built.notes,
            is_material=material,
            ledger_only=built.ledger_only,
            lanes=ValidationLanes(
                evidence=LaneState.FAIL if consistency.failures else LaneState(aligned.lane.value)
            ),
        )

    def map_all(self, assertions: tuple[AssertionBase, ...]) -> tuple[MappingOutcome, ...]:
        return tuple(
            self.map_assertion(assertion, position) for position, assertion in enumerate(assertions)
        )

    def _synonyms(self, text: str, kind: str) -> tuple[str, ...]:
        if kind == "vital_sign":
            return vital_synonyms(text)
        system = (
            RXNORM
            if kind
            in ("explicit_new_order", "reported_medication", "allergy_statement", "allergy_denial")
            else ICD10CM
        )
        return self.terminology.synonyms(text, system)

    def _build(
        self,
        assertion: AssertionBase,
        identifier: str,
        evidence: AssertionEvidenceResult,
        *,
        unit_unusable: bool = False,
    ) -> BuildResult:
        kind = assertion.kind
        if kind == "symptom":
            return build_symptom()
        if kind == "reported_medication":
            return build_reported_medication()
        if kind == "allergy_denial" or (
            kind == "allergy_statement" and assertion.polarity == Polarity.NEGATED
        ):
            result = build_allergy_denial()
            if kind == "allergy_denial" and assertion.polarity != Polarity.NEGATED:
                return result.model_copy(
                    update={"reasons": (*result.reasons, "polarity_kind_mismatch")}
                )
            return result
        if kind == "patient_reported_condition" and assertion.polarity == Polarity.NEGATED:
            return BuildResult(ledger_only=True)
        profile = {
            "clinician_assessed_problem": DIAGNOSIS,
            "clinician_ruled_out_problem": DIAGNOSIS,
            "patient_reported_condition": PROBLEM,
            "explicit_new_order": "us-core-medicationrequest",
            "allergy_statement": "us-core-allergyintolerance",
            "vital_sign": vital_profile(assertion.concept_text),
        }[kind]
        reasons = []
        if assertion.experiencer != Experiencer.PATIENT:
            reasons.append("patient_experiencer_required")
        expected_polarity = (
            Polarity.NEGATED if kind == "clinician_ruled_out_problem" else Polarity.AFFIRMED
        )
        if assertion.polarity != expected_polarity:
            reasons.append("polarity_kind_mismatch")
        if assertion.concept_text is None:
            reasons.append("concept_text_missing")
        roles = {
            segment.role
            for item in assertion.evidence
            if (segment := self.transcript.segment_by_id(item.segment_id)) is not None
        }
        if kind in ("clinician_assessed_problem", "clinician_ruled_out_problem"):
            if (
                assertion.source_speaker_role != SpeakerRole.CLINICIAN
                or SpeakerRole.CLINICIAN not in roles
            ):
                reasons.append("clinician_not_identified")
        if kind == "explicit_new_order":
            assert isinstance(assertion, ExplicitNewOrderAssertion)
            if assertion.medication_intent != MedicationIntent.NEW_ORDER:
                reasons.append("not_explicit_new_order")
            clinicians = {
                segment.speaker_cluster_id
                for segment in self.transcript.segments
                if segment.role == SpeakerRole.CLINICIAN
            }
            if (
                assertion.source_speaker_role != SpeakerRole.CLINICIAN
                or SpeakerRole.CLINICIAN not in roles
                or len(clinicians) != 1
            ):
                reasons.append("prescriber_not_identified")
            if assertion.temporality not in (Temporality.CURRENT, Temporality.FUTURE):
                reasons.append("order_temporality_unclear")
        if reasons:
            return BuildResult(profile=profile, reasons=tuple(reasons))
        match kind:
            case "vital_sign":
                assert isinstance(assertion, VitalSignAssertion)
                return build_vital(
                    assertion,
                    identifier,
                    self.anchors,
                    self.unit_sets,
                    evidence,
                    unit_unusable=unit_unusable,
                )
            case "clinician_assessed_problem":
                return build_assessed(assertion, identifier, self.anchors, self.terminology)
            case "clinician_ruled_out_problem":
                return build_ruled_out(assertion, identifier, self.anchors, self.terminology)
            case "patient_reported_condition":
                return build_reported_condition(
                    assertion, identifier, self.anchors, self.terminology
                )
            case "explicit_new_order":
                assert isinstance(assertion, ExplicitNewOrderAssertion)
                return build_order(
                    assertion, identifier, self.anchors, self.terminology, evidence, self.transcript
                )
            case "allergy_statement":
                assert isinstance(assertion, AllergyStatementAssertion)
                return build_allergy(assertion, identifier, self.anchors, self.terminology)
        raise ValueError("unsupported assertion kind")
