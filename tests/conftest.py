from __future__ import annotations

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "case_ids(*ids): emit stable business case IDs with the final pytest status",
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call":
        return
    marker = item.get_closest_marker("case_ids")
    if marker is None:
        return
    status = "PASSED" if report.passed else "FAILED"
    for case_id in marker.args:
        print(f"CASE {case_id} {status}")
