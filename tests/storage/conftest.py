from __future__ import annotations

import pytest

import logging

from lib import config
from lib.common import randid
from lib.host import Host
from lib.vm import VM
from tests.storage import install_randstream

from typing import Any, Generator

def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # modify ordering so that ext is always tested first,
    # before more complex storage drivers
    for item in reversed(list(items)):
        if "_ext_" in item.path.name:
            items.remove(item)
            items.insert(0, item)

@pytest.fixture(scope='module')
def storage_test_vm(running_unix_vm: VM) -> Generator[VM, None, None]:
    install_randstream(running_unix_vm)
    yield running_unix_vm

@pytest.fixture()
def temp_large_dir(host: Host) -> Generator[str, None, None]:
    """Create a temporary directory on a large (NFS) disk on the primary host"""
    temp_id = randid()
    nfs_conf = config.sr_device_config("NFS_DEVICE_CONFIG")
    if 'serverpath' in nfs_conf or 'server' in nfs_conf:
        mount_path = f'/mnt/nfs_{temp_id}'
        host.ssh(f'mkdir -p {mount_path}')
        path = f'{mount_path}/temp_large_dir_{temp_id}'
        host.ssh(f'mount -t nfs {nfs_conf["server"]}:{nfs_conf["serverpath"]} {mount_path}')
        host.ssh(f'mkdir {path}')
        yield path
        host.ssh(f'rm -rf {path}')
        host.ssh(f'umount {mount_path}')
        host.ssh(f'rmdir {mount_path}')
    else:
        logging.warning("No NFS configuration found, using /tmp")
        path = f'/tmp/temp_large_dir_{temp_id}'
        host.ssh(f'mkdir {path}')
        yield path
        host.ssh(f'rm -rf {path}')
