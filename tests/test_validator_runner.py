"""Runner transport failures never become passing lanes; subprocess is faked."""

import json
import subprocess

import pytest
from validator_helpers import RECORDED, golden_resources

from auris.config import load_frozen_config
from auris.validation.runner import ValidatorRunner, find_java


@pytest.fixture
def runner(tmp_path, monkeypatch):
    instance = ValidatorRunner(load_frozen_config(), tmp_path)
    monkeypatch.setattr(instance, "preflight", lambda: ("fictional-java", tmp_path / "fake.jar"))
    return instance


def _recorded(count=1):
    raw = json.loads((RECORDED / "golden-outcomes.json").read_text())
    raw["entry"] = raw["entry"][: count * 2]
    return raw


def test_both_passes_all_inputs_one_jvm_private_files_and_flags(runner, monkeypatch):
    resources = golden_resources()[:2]
    before = json.dumps(resources, sort_keys=True)
    calls = []
    monkeypatch.setenv("JAVA_TOOL_OPTIONS", "fictional-untrusted-option")

    def run(command, **kwargs):
        calls.append(command)
        work = kwargs["cwd"]
        assert work.stat().st_mode & 0o777 == 0o700
        for index, original in enumerate(resources):
            base = work / f"{index:04d}-base.json"
            profile = work / f"{index:04d}-profile.json"
            assert base.stat().st_mode & 0o777 == 0o600
            assert profile.stat().st_mode & 0o777 == 0o600
            assert "profile" not in json.loads(base.read_text()).get("meta", {})
            assert json.loads(profile.read_text()) == original
        assert (work / "outcomes.json").stat().st_mode & 0o777 == 0o600
        assert "JAVA_TOOL_OPTIONS" not in kwargs["env"]
        assert kwargs["timeout"] == 180
        # Reverse results to prove identity matching is independent of order.
        raw = _recorded(2)
        raw["entry"].reverse()
        (work / "outcomes.json").write_text(json.dumps(raw))
        return subprocess.CompletedProcess(command, 0, "fictional stdout", "fictional stderr")

    monkeypatch.setattr(subprocess, "run", run)
    result = runner.validate(resources)
    assert result.failure is None
    assert len(calls) == 1
    command = calls[0]
    for value in (
        "-Xmx2g",
        "-no-http-access",
        "-disable-default-resource-fetcher",
        "-tx",
        "n/a",
        "-version",
        "4.0.1",
        "-ig",
        "hl7.fhir.us.core#6.1.0",
    ):
        assert value in command
    assert json.dumps(resources, sort_keys=True) == before
    assert result.raw_output and result.stdout == "fictional stdout"
    assert result.stderr == "fictional stderr"
    assert result.wall_seconds >= 0
    assert not list(runner.cache.glob("run-*"))


@pytest.mark.parametrize(
    "failure",
    [
        "missing-java",
        "missing-jar",
        "crash",
        "timeout",
        "empty",
        "malformed",
        "incomplete",
        "unexpected",
        "nonzero-without-errors",
        "validator-timeout",
    ],
)
def test_failure_never_passes(runner, monkeypatch, failure):
    def run(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 180, output=b"partial stdout")
        if failure == "crash":
            return subprocess.CompletedProcess(command, -9, "", "fictional crash")
        raw = _recorded()
        if failure == "validator-timeout":
            raw["entry"][0]["resource"]["issue"].append(
                {
                    "severity": "warning",
                    "code": "processing",
                    "extension": [
                        {
                            "url": "http://hl7.org/fhir/StructureDefinition/operationoutcome-message-id",
                            "valueCode": "VALIDATION_TIMEOUT_EXCEEDED",
                        }
                    ],
                }
            )
        if failure == "incomplete":
            raw["entry"].pop()
        elif failure == "unexpected":
            raw = _recorded(2)
        text = (
            "" if failure == "empty" else ("invalid" if failure == "malformed" else json.dumps(raw))
        )
        (kwargs["cwd"] / "outcomes.json").write_text(text)
        return subprocess.CompletedProcess(
            command, int(failure == "nonzero-without-errors"), "", ""
        )

    monkeypatch.setattr(subprocess, "run", run)
    if failure.startswith("missing"):

        def preflight():
            raise FileNotFoundError(failure)

        monkeypatch.setattr(runner, "preflight", preflight)
    result = runner.validate(golden_resources()[:1])
    assert result.failure
    resource = result.resources[0]
    assert resource.base_r4.state == resource.us_core_profile.state == "NOT_CHECKED"
    assert resource.terminology.state == "NOT_CHECKED"
    assert resource.base_r4.reason == result.failure
    if failure == "timeout":
        assert result.stdout == "partial stdout"


def test_error_exit_with_complete_outcomes_is_validation_failure_not_runner_failure(
    runner, monkeypatch
):
    raw = json.loads((RECORDED / "broken-outcomes.json").read_text())
    raw["entry"] = raw["entry"][:2]

    def run(command, **kwargs):
        (kwargs["cwd"] / "outcomes.json").write_text(json.dumps(raw))
        return subprocess.CompletedProcess(command, 1, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    result = runner.validate(golden_resources()[:1])
    assert result.failure is None
    assert result.resources[0].base_r4.state == "FAIL"


def test_real_preflight_missing_jar_is_not_checked(tmp_path, monkeypatch):
    monkeypatch.setattr("auris.validation.runner.find_java", lambda: "fictional-java")
    result = ValidatorRunner(load_frozen_config(), tmp_path).validate(golden_resources()[:1])
    assert "JAR missing" in result.failure
    assert result.resources[0].base_r4.state == "NOT_CHECKED"


def test_java_precedence_and_explicit_invalid_override(tmp_path, monkeypatch):
    for key in ("AURIS_JAVA", "JAVA_HOME"):
        monkeypatch.delenv(key, raising=False)
    executables = []
    for name in ("explicit", "home/bin/java", "path/java"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fictional executable; never run")
        path.chmod(0o700)
        executables.append(path)
    explicit, home, fallback = executables
    monkeypatch.setenv("PATH", str(fallback.parent))
    assert find_java() == str(fallback)
    monkeypatch.setenv("JAVA_HOME", str(home.parent.parent))
    assert find_java() == str(home)
    monkeypatch.setenv("AURIS_JAVA", str(explicit))
    assert find_java() == str(explicit)
    monkeypatch.setenv("AURIS_JAVA", str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError):
        find_java()


def test_duplicate_inputs_refused_and_empty_run_does_not_start_java(runner, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kw: pytest.fail("must not run"))
    assert runner.validate([]).resources == ()
    resource = golden_resources()[0]
    with pytest.raises(ValueError, match="duplicate"):
        runner.validate([resource, resource])


def test_separate_workdir_keeps_inputs_private_and_reuses_package_home(
    runner, tmp_path, monkeypatch
):
    work_parent = tmp_path / "private-work"
    work_parent.mkdir(mode=0o700)
    runner.work_directory = work_parent

    def run(command, **kwargs):
        work = kwargs["cwd"]
        assert work.parent == work_parent
        assert f"-Duser.home={runner.cache / 'home'}" in command
        assert f"-Djava.io.tmpdir={work}" in command
        assert not (work / "home").exists()  # no per-check copy of the package cache
        (work / "outcomes.json").write_text(json.dumps(_recorded()))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    result = runner.validate(golden_resources()[:1])
    assert result.failure is None
    assert not list(work_parent.iterdir())
