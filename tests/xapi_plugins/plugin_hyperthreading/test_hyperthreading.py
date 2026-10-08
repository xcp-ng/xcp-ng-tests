import pytest

from lib.host import Host

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host > 8.2.

@pytest.mark.usefixtures("fail_with_v9") # TBD xcp-ng-xapi-plugins is not compatible
def test_get_hyperthreading(host: Host) -> None:
    host.call_plugin('hyperthreading.py', 'get_hyperthreading')
