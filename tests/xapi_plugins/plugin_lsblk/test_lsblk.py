import pytest

from lib.host import Host

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host > 8.2.

@pytest.mark.usefixtures("xfail_on_xcpng_9_0") # TBD xcp-ng-xapi-plugins is not compatible
def test_list_block_devices(host: Host) -> None:
    host.call_plugin('lsblk.py', 'list_block_devices')
