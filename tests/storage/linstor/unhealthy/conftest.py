from __future__ import annotations

import pytest

import json
import logging

from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from lib.host import Host
    from lib.pool import Pool

DM_FLAKEY_DEV_NAME = 'linfail'

class FlakeyDisk:
    def __init__(
        self,
        host: Host,
        device: Host.BlockDeviceInfo,
        dm_dev_name: str,
    ) -> None:
        self._host = host
        self._device = device
        self._dm_dev_name = dm_dev_name

    @property
    def path(self) -> str:
        return f'/dev/mapper/{self._dm_dev_name}'

    def create(self) -> None:
        self._host.ssh(f'dmsetup create {self._dm_dev_name} --table "{self._build_dm_table(False)}"')

    def remove(self) -> None:
        self._host.ssh(f'dmsetup remove {self._dm_dev_name}')

    def fail(self) -> None:
        logging.info(f'Failing device {self._device.path} on {self._host.hostname_or_ip}')

        self._apply_dm_table(self._build_dm_table(True))
        self._host.ssh('sync')
        self._host.ssh('echo 3 > /proc/sys/vm/drop_caches')

    def repair(self) -> None:
        logging.info(f'Repairing device {self._device.path} on {self._host.hostname_or_ip}')

        self._apply_dm_table(self._build_dm_table(False))
        cmd_res = self._host.ssh('linstor -m --controllers `xe host-list params=address --minimal` r l -n `hostname` --props DrbdOptions/SkipDisk') # noqa: E501
        failing_resources = json.loads(cmd_res)

        # Make sure we take care of the database first so repairing the other
        # resources doesn't hang or fail.
        failing_resource_names = [res['name'] for res in failing_resources[0]]
        failing_resource_names.sort(key=lambda x: x != 'xcp-persistent-database')

        for failing_resource_name in failing_resource_names:
            self._host.ssh(f'linstor --controllers `xe host-list params=address --minimal` r sp `hostname` {failing_resource_name} DrbdOptions/SkipDisk') # noqa: E501
            self._host.ssh(f'drbdadm wait-sync {failing_resource_name}')

    def _build_dm_table(self, disk_failed: bool) -> str:
        disk_size = self._device.size // 512

        if disk_failed:
            return f'0 {disk_size} flakey {self._device.path} 0 0 1'
        else:
            return f'0 {disk_size} flakey {self._device.path} 0 1 0'

    def _apply_dm_table(self, table: str) -> None:
        self._host.ssh(f'dmsetup reload {self._dm_dev_name} --table "{table}"')
        self._host.ssh(f'dmsetup resume {self._dm_dev_name}')

@pytest.fixture(scope='package')
def flakey_unused_512B_disk(
    pool_with_unused_512B_disk: Pool,
    unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]],
) -> Generator[dict[Host, FlakeyDisk], None, None]:
    flakey_disks: dict[Host, FlakeyDisk] = {}
    hosts = pool_with_unused_512B_disk.hosts

    for host in hosts:
        disk = unused_512B_disks[host][0]
        flakey_disk = FlakeyDisk(host, disk, DM_FLAKEY_DEV_NAME)
        flakey_disk.create()

        flakey_disks[host] = flakey_disk

    yield flakey_disks

    for flakey_disk in flakey_disks.values():
        flakey_disk.remove()

@pytest.fixture(scope='package')
def lvm_disk_paths(
    flakey_unused_512B_disk: dict[Host, FlakeyDisk],
) -> dict[Host, list[str]]:
    # Overrides the `lvm_disk_paths` package-scoped fixture from the parent
    # package so we can transparently use other fixtures that depend on it
    # whilst having a dm-flakey device mapper underneath.
    return {host: [disk.path] for (host, disk) in flakey_unused_512B_disk.items()}
