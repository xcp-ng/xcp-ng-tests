import json
import logging
import pytest

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host > 8.2 with an additional unused disk for the SR.
# And:
# - access to XCP-ng RPM repository from hostA1

@pytest.fixture(scope='module')
def host_with_zfs(host):
    assert not host.file_exists('/usr/sbin/zpool'), \
        "zfs must not be installed on the host at the beginning of the tests"
    host.yum_save_state()
    host.yum_install(['zfs'])
    host.ssh(['modprobe', 'zfs'])
    yield host
    # teardown
    host.yum_restore_saved_state()

@pytest.fixture(scope='module')
def zpool_vol0(sr_disk_wiped, host_with_zfs):
    host_with_zfs.ssh(['zpool', 'create', '-f', 'vol0', '/dev/' + sr_disk_wiped])
    yield
    # teardown
    host_with_zfs.ssh(['zpool', 'destroy', 'vol0'])

@pytest.mark.usefixtures("zpool_vol0")
def test_list_zfs_pools(host):
    logging.info("List ZFS pools on host")
    res = host.call_plugin('zfs.py', 'list_zfs_pools')
    assert json.loads(res).get("vol0") is not None
