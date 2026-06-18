from __future__ import annotations

import pytest

from lib.host import Host
from lib.vm import VM
from tests.ha.ha import (
    host_rejoin_test,
    master_failure_test,
    slave_failure_test,
)

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host in a pool with HA-capable networking
# - hostA2: second member of the pool
# From data.py:
# - NFS_DEVICE_CONFIG with a shared NFS export (used for VM disks and HA heartbeat)
# From --vm parameter:
# - a small agile linux VM importable on the NFS SR

@pytest.mark.complex_prerequisites
@pytest.mark.reboot
@pytest.mark.small_vm
class TestHaScenario:
    def test_master_failure(
        self, host: Host, hostA2: Host, ha_protected_vm: VM
    ) -> None:
        master_failure_test(host, hostA2, ha_protected_vm)

    def test_slave_failure(
        self, host: Host, hostA2: Host, ha_protected_vm: VM
    ) -> None:
        slave_failure_test(host, hostA2, ha_protected_vm)

    def test_host_rejoin(
        self, host: Host, hostA2: Host, ha_protected_vm: VM
    ) -> None:
        host_rejoin_test(host, hostA2, ha_protected_vm)
