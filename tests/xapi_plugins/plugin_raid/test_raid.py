import pytest

import logging

from lib.host import Host

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host > 8.2.

@pytest.fixture(scope='module')
def host_with_raid(host: Host):
    dummy_raid = False
    if not host.file_exists('/dev/md127', regular_file=False):
        logging.info("> Host has no raids, creating one for tests")
        dummy_raid = True
        host.ssh('dd if=/dev/zero of=raid-0 bs=1M count=200')
        host.ssh('cp raid-0 raid-1')
        host.ssh('losetup /dev/loop0 raid-0')
        host.ssh('losetup /dev/loop1 raid-1')
        host.ssh('mdadm --create /dev/md127 --run --level=mirror --raid-devices=2 /dev/loop0 /dev/loop1')

    yield host

    if dummy_raid:
        logging.info("< Destroying raid created for tests")
        host.ssh('mdadm --stop --force /dev/md127')
        host.ssh('losetup -d /dev/loop0')
        host.ssh('losetup -d /dev/loop1')
        host.ssh('rm -rf raid-1 raid-0')

def test_check_raid_pool(host_with_raid):
    host = host_with_raid
    host.call_plugin('raid.py', 'check_raid_pool')
