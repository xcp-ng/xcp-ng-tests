# Requirements:
# - --hosts: host(A1) + hostA2 in a 2-node pool
# - data.py: NFS_DEVICE_CONFIG (shared NFS for VM disks + HA heartbeat)
# - --vm: small agile linux VM
# - tests/ha/data.py: Moonshot CARTRIDGES map (power-off recovery)
#   cp tests/ha/data.py-dist tests/ha/data.py
from __future__ import annotations

import pytest

from lib.host import Host
from lib.sr import SR
from lib.vm import VM
from tests.ha.ha import (
    run_host_failure_test,
    run_management_network_cut_test,
    run_nfs_unreachable_one_host_test,
    run_nfs_unreachable_pool_test,
    run_xapi_crash_test,
    run_xhad_crash_test,
)

@pytest.mark.complex_prerequisites
@pytest.mark.small_vm
@pytest.mark.filterwarnings('ignore::urllib3.exceptions.InsecureRequestWarning')
class TestHaScenario:
    @pytest.mark.reboot
    def test_master_failure(
        self,
        host: Host,
        hostA2: Host,
        ha_protected_vm: VM,
        nfs_sr: SR,
    ) -> None:
        run_host_failure_test(
            host.pool, ha_protected_vm, nfs_sr,
            failed_host=host, survivor=hostA2,
        )

    @pytest.mark.reboot
    def test_slave_failure(
        self,
        host: Host,
        hostA2: Host,
        ha_protected_vm: VM,
        nfs_sr: SR,
    ) -> None:
        run_host_failure_test(
            host.pool, ha_protected_vm, nfs_sr,
            failed_host=hostA2, survivor=host,
        )

    @pytest.mark.reboot
    def test_master_failure_with_recovery(
        self,
        host: Host,
        hostA2: Host,
        ha_protected_vm: VM,
        nfs_sr: SR,
    ) -> None:
        run_host_failure_test(
            host.pool, ha_protected_vm, nfs_sr,
            failed_host=host, survivor=hostA2,
            assert_recovery=True,
        )

    @pytest.mark.reboot
    def test_slave_failure_with_recovery(
        self,
        host: Host,
        hostA2: Host,
        ha_protected_vm: VM,
        nfs_sr: SR,
    ) -> None:
        run_host_failure_test(
            host.pool, ha_protected_vm, nfs_sr,
            failed_host=hostA2, survivor=host,
            assert_recovery=True,
        )

    @pytest.mark.reboot
    def test_xhad_crash_on_slave(
        self,
        host: Host,
        hostA2: Host,
        ha_protected_vm: VM,
        nfs_sr: SR,
    ) -> None:
        run_xhad_crash_test(
            host.pool, ha_protected_vm, nfs_sr,
            failed_host=hostA2, survivor=host,
        )

    @pytest.mark.reboot
    def test_xhad_crash_on_master(
        self,
        host: Host,
        hostA2: Host,
        ha_protected_vm: VM,
        nfs_sr: SR,
    ) -> None:
        run_xhad_crash_test(
            host.pool, ha_protected_vm, nfs_sr,
            failed_host=host, survivor=hostA2,
        )

    def test_xapi_crash_on_slave(
        self,
        host: Host,
        hostA2: Host,
        ha_protected_vm: VM,
        nfs_sr: SR,
    ) -> None:
        run_xapi_crash_test(
            host.pool, ha_protected_vm, nfs_sr,
            target_host=hostA2, other_host=host,
        )

    def test_xapi_crash_on_master(
        self,
        host: Host,
        hostA2: Host,
        ha_protected_vm: VM,
        nfs_sr: SR,
    ) -> None:
        run_xapi_crash_test(
            host.pool, ha_protected_vm, nfs_sr,
            target_host=host, other_host=hostA2,
        )

    def test_nfs_unreachable_pool(
        self,
        host: Host,
        hostA2: Host,
        ha_protected_vm: VM,
        nfs_sr: SR,
    ) -> None:
        run_nfs_unreachable_pool_test(
            host.pool, ha_protected_vm, nfs_sr,
            vm_host=host, other_host=hostA2,
        )

    @pytest.mark.reboot
    def test_nfs_unreachable_one_host(
        self,
        host: Host,
        hostA2: Host,
        ha_protected_vm: VM,
        nfs_sr: SR,
    ) -> None:
        run_nfs_unreachable_one_host_test(
            host.pool, ha_protected_vm, nfs_sr,
            blocked_host=hostA2, survivor=host,
        )

    @pytest.mark.reboot
    def test_management_network_cut_one_host(
        self,
        host: Host,
        hostA2: Host,
        ha_protected_vm: VM,
        nfs_sr: SR,
    ) -> None:
        run_management_network_cut_test(
            host.pool, ha_protected_vm, nfs_sr,
            cut_host=hostA2, survivor=host,
        )
