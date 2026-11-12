"""Official pinned oracle on fictional golden and deliberately broken resources."""

import subprocess
from collections import Counter

import pytest
from fhir_helpers import GOLDENS
from validator_helpers import RECORDED, broken_resources, golden_resources

from auris.config import load_frozen_config
from auris.fhir import build_collection
from auris.fhir.models import LaneState
from auris.setup.validator import default_cache_dir
from auris.validation.outcomes import classify, parse_outcomes
from auris.validation.report import validate_mappings, write_validation
from auris.validation.runner import ValidatorRunner, find_java

pytestmark = pytest.mark.validator


@pytest.fixture(scope="module")
def runner():
    config = load_frozen_config()
    cache = default_cache_dir()
    if not (cache / config.validator.filename).is_file():
        pytest.skip("pinned validator JAR unavailable; run auris setup validator")
    try:
        java = find_java()
        result = subprocess.run([java, "-version"], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"Java unavailable: {exc}")
    if result.returncode:
        pytest.skip("Java runtime unavailable; set AURIS_JAVA or JAVA_HOME")
    # A present but corrupt JAR or stale package seed is a failure, not a skip.
    instance = ValidatorRunner(config, cache, allow_example_urls=True)
    instance.preflight()
    return instance


def test_all_golden_resources_and_intake_pass(runner, map_case, tmp_path, record_property):
    mapped = [map_case(c["name"]) for c in GOLDENS]
    intake = mapped[0][0].intake
    outcomes, run = validate_mappings(runner, intake, [o for _, _, o in mapped])
    record_property("validator_seconds", f"goldens + anchors: {run.wall_seconds:.3f}s")
    assert run.failure is None, run.failure
    assert len(run.resources) == len(golden_resources()) + 3
    for result in run.resources:
        assert result.base_r4.state == LaneState.PASS, result
        assert result.us_core_profile.state == LaneState.PASS, result
        assert result.terminology.state == LaneState.NOT_CHECKED
    # The recorded baseline orders clinical resources first, then anchors.
    # Compare all issue severities, including warnings, without rewriting it.
    recorded = parse_outcomes((RECORDED / "golden-outcomes.json").read_text())
    resources = [o.resource for o in outcomes if o.resource is not None]
    for index, resource in enumerate(resources):
        before = classify(
            resource, recorded[f"{index:04d}-base.json"], recorded[f"{index:04d}-profile.json"]
        )
        after = run.for_resource(resource)
        for lane in ("base_r4", "us_core_profile", "terminology"):
            previous = Counter(i.identity for i in getattr(before, lane).issues)
            current = Counter(i.identity for i in getattr(after, lane).issues)
            assert not (current - previous), (resource["resourceType"], lane, current - previous)
    eligible = [o for o in outcomes if o.export_state == "final_exportable"]
    assert eligible
    bundle = build_collection(intake, eligible, validation=run)
    assert len(bundle["entry"]) == len(eligible) + 3
    target = tmp_path / "report" / "validation.json"
    write_validation(target, intake, outcomes, run)
    assert '"label": "validated"' not in target.read_text()


def test_demo_instruction_and_display_variants_pass_official_validator(
    runner, map_case, terminology, units, record_property
):
    from demo_helpers import demo_input

    from auris.fhir import Mapper

    transcript, intake, assertions = demo_input()
    demo = Mapper(intake, transcript, terminology, units).map_all(assertions)
    variants = [
        map_case(name, changes={"unit_text": unit, "value_text": value}, quote=quote)[2]
        for name, unit, value, quote in (
            (
                "body-temperature",
                "degrees Fahrenheit",
                "98.6",
                "Your temperature is 98.6 degrees Fahrenheit.",
            ),
            ("body-weight", "pounds", "154", "Your weight is 154 pounds."),
            ("body-height", "inches", "67.5", "Your height is 67.5 inches."),
        )
    ]
    for outcome, display, code in zip(
        variants, ("°F", "lb", "in"), ("[degF]", "[lb_av]", "[in_i]"), strict=True
    ):
        assert outcome.resource["valueQuantity"]["unit"] == display
        assert outcome.resource["valueQuantity"]["code"] == code
    order = next(o for o in demo if o.kind == "explicit_new_order")
    assert order.resource["dosageInstruction"] == [
        {"text": "500 milligrams by mouth every eight hours as needed"}
    ]
    _, run = validate_mappings(runner, intake, (*demo, *variants))
    record_property("validator_seconds", f"demo + display variants: {run.wall_seconds:.3f}s")
    assert run.failure is None, run.failure
    for result in run.resources:
        assert result.base_r4.state == LaneState.PASS, result
        assert result.us_core_profile.state == LaneState.PASS, result
        assert result.terminology.state == LaneState.NOT_CHECKED


def test_broken_resources_fail_in_checker_scope(runner, record_property):
    run = runner.validate(broken_resources())
    record_property("validator_seconds", f"broken resources: {run.wall_seconds:.3f}s")
    assert run.failure is None, run.failure
    allergy, bp_root, bp_slice, medication, condition, vital = run.resources
    assert allergy.base_r4.state == LaneState.FAIL
    assert any("ait-1" in (i.message_id or "") for i in allergy.base_r4.issues)
    assert medication.base_r4.state == LaneState.FAIL
    # US Core repeats the inherited intent cardinality in its own context.
    assert medication.us_core_profile.state == LaneState.FAIL
    assert condition.base_r4.state == LaneState.PASS
    assert condition.us_core_profile.state == LaneState.FAIL
    for result in (bp_root, bp_slice, vital):
        assert result.us_core_profile.state == LaneState.FAIL, result
    assert all(r.terminology.state == LaneState.NOT_CHECKED for r in run.resources)
