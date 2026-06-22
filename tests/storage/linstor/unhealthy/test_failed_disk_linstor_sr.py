from __future__ import annotations

import pytest

import logging
import random

from lib.host import Host
from lib.sr import SR
from lib.vm import VM

from .conftest import FlakeyDisk

# Requirements:
# - three or more XCP-ng hosts >= 8.2 with additional unused disk(s) for the SR
# - access to XCP-ng RPM repository from the host

class TestLinstorSRFailedDisk:
    @pytest.mark.small_vm # run with a small VM to test the features
    def test_linstor_sr_fail_disk(
        self,
        vm_on_linstor_sr: VM,
        flakey_unused_512B_disk: dict[Host, FlakeyDisk],
        linstor_sr: SR,
    ) -> None:
        sr = linstor_sr
        vm = vm_on_linstor_sr
        random_host = random.choice(sr.pool.hosts)

        # Let xcp-persistent-database come in sync across the nodes.
        random_host.ssh('drbdadm wait-sync xcp-persistent-database')

        flakey_unused_512B_disk[random_host].fail()

        try:
            for host in sr.pool.hosts:
                logging.info(f'Checking VM on host {host.hostname_or_ip}')

                vm.start(on=host.uuid)
                vm.wait_for_os_booted()
                vm.shutdown(verify=True)
        finally:
            flakey_unused_512B_disk[random_host].repair()
