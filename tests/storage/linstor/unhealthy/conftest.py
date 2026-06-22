from __future__ import annotations

import pytest

import json
import logging

import lib.commands as commands

# explicit import for package-scope fixtures
from tests.storage.linstor.pkgfixtures import (
    _linstor_config,
    linstor_redundancy,
    linstor_sr,
    lvm_disks,
    pool_with_linstor,
    storage_pool_name,
)

from typing import TYPE_CHECKING, Generator, List

if TYPE_CHECKING:
    from lib.host import Host
    from lib.pool import Pool

DM_FLAKEY_DEV_NAME = 'linfail'

class FlakeyDisk:
    def __init__(
        self,
        host: Host,
        pool_hosts: List[Host],
        device: Host.BlockDeviceInfo,
        dm_dev_name: str,
    ) -> None:
        self._host = host
        self._pool_hosts = pool_hosts
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
        cmd_res = self._host.ssh('linstor -m --controllers `xe host-list params=address --minimal` r l'
                                 ' -n `hostname` --props DrbdOptions/SkipDisk')
        failing_resources = json.loads(cmd_res)

        # Make sure we take care of the database first so repairing the other
        # resources doesn't hang or fail.
        failing_resource_names = [res['name'] for res in failing_resources[0]]
        failing_resource_names.sort(key=lambda x: x != 'xcp-persistent-database')

        for failing_resource_name in failing_resource_names:
            try:
                self._host.ssh('linstor --controllers `xe host-list params=address --minimal` r sp'
                               f' `hostname` {failing_resource_name} DrbdOptions/SkipDisk')
            except commands.SSHCommandFailed:
                drbd_res_status = json.loads(self._host.ssh(f'drbdsetup status --json {failing_resource_name}'))

                if drbd_res_status[0]['devices'][0]['disk-state'] != 'Negotiating':
                    raise

                # The DRBD resource sometimes gets stuck in the "Negotiating"
                # state when repairing the device. This can lead to a split-brain
                # issue, so we try to fix it as one.
                logging.warning('DRBD resource stuck in Negotiating state; attempting fix')

                self._host.ssh(f'drbdadm disconnect {failing_resource_name}')
                self._host.ssh(f'drbdadm connect {failing_resource_name}')

                for other_host in filter(lambda x: x != self._host, self._pool_hosts):
                    drbd_res_status = json.loads(other_host.ssh(f'drbdsetup status --json {failing_resource_name}'))

                    other_host.ssh(f'drbdadm disconnect {failing_resource_name}')

                    if drbd_res_status[0]['role'] == 'Primary':
                        other_host.ssh(f'drbdadm connect {failing_resource_name}')
                    else:
                        other_host.ssh(f'drbdadm connect --discard-my-data {failing_resource_name}')

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
        flakey_disk = FlakeyDisk(host, hosts, disk, DM_FLAKEY_DEV_NAME)
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
