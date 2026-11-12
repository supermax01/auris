"""Official pinned oracle on fictional golden and deliberately broken resources."""

import subprocess

import pytest
from fhir_helpers import GOLDENS
from validator_helpers import broken_resources, golden_resources

from auris.config import load_frozen_config
from auris.fhir import build_collection
from auris.fhir.models import LaneState
from auris.setup.validator import default_cache_dir
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
    eligible = [o for o in outcomes if o.export_state == "final_exportable"]
    assert eligible
    bundle = build_collection(intake, eligible, validation=run)
    assert len(bundle["entry"]) == len(eligible) + 3
    target = tmp_path / "report" / "validation.json"
    write_validation(target, intake, outcomes, run)
    assert '"label": "validated"' not in target.read_text()


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
