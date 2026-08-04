from __future__ import annotations

import pytest

import logging
import random

from lib.common import Defer, run_with_timeout
from lib.host import Host
from lib.sr import SR
from lib.vm import VM

from .conftest import FlakeyDisk

# Requirements:
# - three or more XCP-ng hosts >= 8.2 with additional unused disk(s) for the SR
# - LINSTOR redundancy set to at least 2
# - access to XCP-ng RPM repository from the host

class TestLinstorSRFailedDisk:
    @pytest.mark.small_vm # run with a small VM to test the features
    def test_linstor_sr_fail_disk(
        self,
        vm_on_linstor_sr: VM,
        flakey_unused_512B_disk: dict[Host, FlakeyDisk],
        linstor_sr: SR,
        defer: Defer
    ) -> None:
        sr = linstor_sr
        vm = vm_on_linstor_sr
        failed_host = sr.pool.hosts[0]

        # Let xcp-persistent-database come in sync across the nodes.
        failed_host.ssh('drbdadm wait-sync xcp-persistent-database')

        flakey_unused_512B_disk[failed_host].fail()
        defer(lambda: flakey_unused_512B_disk[failed_host].repair())

        for host in sr.pool.hosts:
            logging.info(f'Checking VM on host {host.hostname_or_ip}')

            run_with_timeout(lambda: vm.start(on=host.uuid), timeout_secs=60)
            vm.wait_for_os_booted()
            vm.shutdown(verify=True)
