"""Shared offline mapper fixtures and per-A3-row passing-test counts."""

from collections import Counter

import pytest
from checklist_assertions import check_resource
from fhir_helpers import mapped_case
from terminology_helpers import fictional_intake, fixture_unit_sets, write_archives

from auris.setup.terminology import setup_terminology
from auris.terminology import TerminologyIndex


@pytest.fixture(scope="session")
def terminology(tmp_path_factory):
    cache = tmp_path_factory.mktemp("terminology")
    path = setup_terminology(write_archives(cache), cache)
    with TerminologyIndex(path) as index:
        yield index


@pytest.fixture
def intake():
    return fictional_intake()


@pytest.fixture
def units():
    return fixture_unit_sets()


@pytest.fixture
def map_case(terminology, units, intake, request):
    def run(name, **kwargs):
        selected_intake = kwargs.pop("intake", intake)
        mapper, assertion, result = mapped_case(name, terminology, units, selected_intake, **kwargs)
        request.node.user_properties.append(("a3_row", assertion.kind))
        if result.resource is not None:
            check_resource(result.resource)
        return mapper, assertion, result

    return run


def pytest_terminal_summary(terminalreporter):
    for report in terminalreporter.stats.get("passed", []):
        for key, value in report.user_properties:
            if key == "validator_seconds":
                terminalreporter.write_line(f"Validator wall-clock time — {value}")
    counts = Counter()
    for report in terminalreporter.stats.get("passed", []):
        counts.update({value for key, value in report.user_properties if key == "a3_row"})
    if counts:
        terminalreporter.write_line("A3 mapper coverage (passing test cases per row):")
        for kind, count in sorted(counts.items()):
            terminalreporter.write_line(f"  {kind}: {count}")
