import json

from tests.storage.zfs.conftest import zpool_vol0, host_with_zfs

def test_list_zfs_pools(host, zpool_vol0):
    res = host.call_plugin('zfs.py', 'list_zfs_pools')
    assert json.loads(res).get("vol0") is not None
