# ruff: noqa: SLF001 - this module deliberately tests private conftest internals
from __future__ import annotations

import pytest

from unittest.mock import MagicMock

from _pytest.nodeid import NodeId
from _pytest.stash import StashKey
from packaging import version

import conftest

from typing import Literal

TICKET = "XCPNG-1234"
Phase = Literal["setup", "call", "teardown"]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def make_stash(host_version: str | None) -> pytest.Stash:
    stash = pytest.Stash()
    if host_version is not None:
        host = MagicMock()
        host.xcp_version = version.parse(host_version)
        stash[conftest.HOSTA1_KEY] = host
    return stash

def make_item(host_version: str | None, *, with_marker: bool = True,
              runxfail: bool = False, nodeid: str = "tests/fake/test_fake.py::test_fake") -> MagicMock:
    item = MagicMock(spec=pytest.Item)
    item.nodeid = nodeid
    item.keywords = {}
    item.id = NodeId.parse(nodeid)
    item.location = ("tests/fake/test_fake.py", 0, "test_fake")
    item.config = MagicMock()
    item.config.option.runxfail = runxfail
    item.config.stash = make_stash(host_version)
    item.stash = pytest.Stash()
    item._report_sections = []
    item.user_properties = []
    if with_marker:
        marker = MagicMock()
        marker.args = (TICKET,)
        item.get_closest_marker.return_value = marker
    else:
        item.get_closest_marker.return_value = None
    return item

def make_report(item: MagicMock, when: Phase, failed: bool = False) -> pytest.TestReport:
    """Build a report as pytest would, from an item and a call."""
    call: pytest.CallInfo[None]
    if failed:
        def raising() -> None:
            raise AssertionError("fake failure")
        call = pytest.CallInfo.from_call(raising, when=when, reraise=())
    else:
        call = pytest.CallInfo.from_call(lambda: None, when=when, reraise=())
    return pytest.TestReport.from_item_and_call(item, call)

def run_makereport(item: MagicMock, report: pytest.TestReport) -> pytest.TestReport:
    """Drive the conftest wrapper hook with the given report."""
    call = pytest.CallInfo.from_call(lambda: None, when=report.when, reraise=())
    wrapper = conftest.pytest_runtest_makereport(item, call)
    next(wrapper)  # start the hook, "executing all other hooks"
    try:
        wrapper.send(report)
    except StopIteration as stop:
        assert stop.value is not None
        return stop.value
    raise AssertionError("makereport hook generator did not return a report")


# ---------------------------------------------------------------------------
# _xfail_on_xcpng_9_0_reason
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("host_version, with_marker, expected_reason", [
    (None, True, None),                      # host fixture not resolved yet
    ("8.2.1", True, None),                    # below the 8.99 threshold
    ("8.99", True, f"xfail_on_xcpng_9_0 (version 8.99), see {TICKET}"),
    ("9.0", True, f"xfail_on_xcpng_9_0 (version 9.0), see {TICKET}"),
    ("8.99", False, None),                    # marker not present
    (None, False, None),
])
def test_xfail_on_xcpng_9_0_reason(host_version: str | None, with_marker: bool, expected_reason: str | None) -> None:
    item = make_item(host_version, with_marker=with_marker)
    assert conftest._xfail_on_xcpng_9_0_reason(item) == expected_reason


# ---------------------------------------------------------------------------
# pytest_runtest_makereport
# ---------------------------------------------------------------------------

def test_setup_failure_is_converted_to_xfail() -> None:
    item = make_item("8.99")
    report = make_report(item, "setup", failed=True)
    assert report.failed

    result = run_makereport(item, report)

    assert result.outcome == "skipped"
    assert result.wasxfail == f"xfail_on_xcpng_9_0 (version 8.99), see {TICKET}"
    # the traceback is kept so the failure is still visible in the report
    assert result.longrepr is not None

def test_call_failure_is_converted_to_xfail() -> None:
    item = make_item("8.99")
    report = make_report(item, "call", failed=True)

    result = run_makereport(item, report)

    assert result.outcome == "skipped"
    assert result.wasxfail == f"xfail_on_xcpng_9_0 (version 8.99), see {TICKET}"

def test_call_pass_is_converted_to_xpass() -> None:
    item = make_item("8.99")
    report = make_report(item, "call")

    result = run_makereport(item, report)

    assert result.outcome == "passed"
    assert result.wasxfail == f"xfail_on_xcpng_9_0 (version 8.99), see {TICKET}"

@pytest.mark.parametrize("host_version", [None, "8.2.1"])
def test_below_threshold_report_is_untouched(host_version: str | None) -> None:
    item = make_item(host_version)
    report = make_report(item, "call", failed=True)

    result = run_makereport(item, report)

    assert result.failed
    assert not hasattr(result, "wasxfail")

def test_without_marker_report_is_untouched() -> None:
    item = make_item("8.99", with_marker=False)
    report = make_report(item, "call", failed=True)

    result = run_makereport(item, report)

    assert result.failed
    assert not hasattr(result, "wasxfail")

def test_setup_pass_is_untouched_even_with_marker() -> None:
    item = make_item("8.99")
    report = make_report(item, "setup")

    result = run_makereport(item, report)

    assert result.passed
    assert not hasattr(result, "wasxfail")

def test_teardown_failure_is_untouched() -> None:
    item = make_item("8.99")
    report = make_report(item, "teardown", failed=True)

    result = run_makereport(item, report)

    assert result.failed
    assert not hasattr(result, "wasxfail")

def test_runxfail_disables_conversion() -> None:
    item = make_item("8.99", runxfail=True)
    report = make_report(item, "call", failed=True)

    result = run_makereport(item, report)

    assert result.failed
    assert not hasattr(result, "wasxfail")

def test_native_xfail_report_is_untouched() -> None:
    item = make_item("8.99")
    report = make_report(item, "call", failed=True)
    report.wasxfail = "native xfail reason"

    result = run_makereport(item, report)

    assert result.outcome == "failed"
    assert result.wasxfail == "native xfail reason"


# ---------------------------------------------------------------------------
# Stash key uniqueness: the key must be a StashKey, not a plain value
# ---------------------------------------------------------------------------

def test_hosta1_key_is_stash_key() -> None:
    assert isinstance(conftest.HOSTA1_KEY, StashKey)
