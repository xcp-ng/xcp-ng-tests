import json
import logging

import pytest

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host > 8.2 with an additional unused disk for the SR.
# And:
# - access to XCP-ng RPM repository from hostA1

@pytest.mark.usefixtures("zpool_vol0")
def test_list_zfs_pools(host):
    logging.info("List ZFS pools on host")
    res = host.call_plugin('zfs.py', 'list_zfs_pools')
    assert json.loads(res).get("vol0") is not None
