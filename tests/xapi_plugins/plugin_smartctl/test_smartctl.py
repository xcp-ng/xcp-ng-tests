import pytest

import json

from lib.host import Host

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.3.

def _call_plugin(host: Host, fn: str) -> None:
    ret = host.call_plugin("smartctl.py", fn)
    try:
        json.loads(ret)
    except ValueError:
        pytest.fail("JSON string was expected but ValueError was raised")

@pytest.mark.usefixtures("host_at_least_8_3")
def test_smartctl_information(host: Host) -> None:
    _call_plugin(host, "information")

@pytest.mark.usefixtures("host_at_least_8_3")
def test_smartctl_health(host: Host) -> None:
    _call_plugin(host, "health")
