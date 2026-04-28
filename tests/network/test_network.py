from __future__ import annotations

import pytest

import logging

from lib.host import Host
from lib.network import Network
from lib.vm import VM

class TestNetwork:
    @pytest.mark.no_vm
    def test_empty_network(self, host: Host, empty_network: Network):
        assert empty_network.pif_uuids() == [], "PIF list must be empty"
        assert empty_network.vif_uuids() == [], "VIF list must be empty"
        assert empty_network.is_private(), "empty_network must be private"
        assert empty_network.MTU() == 1500, "expected MTU is 1500"

    @pytest.mark.small_vm
    def test_private_network(self, host: Host, empty_network: Network, imported_vm: VM):
        network = empty_network

        vm1 = imported_vm.clone()
        vm2 = imported_vm.clone()
        try:
            vif_1_1 = vm1.create_vif(1, network_uuid=network.uuid)
            vif_2_1 = vm2.create_vif(1, network_uuid=network.uuid)

            assert len(vm1.vifs()) == 2, "VM1 should have 2 NICs"
            assert len(vm2.vifs()) == 2, "VM2 should have 2 NICs"
            assert len(network.vif_uuids()) == 2, "network have 2 VIFs"

            vm1.start()
            vm2.start()

            vm1.wait_for_vm_running_and_ssh_up()
            vm2.wait_for_vm_running_and_ssh_up()

            logging.info("Configuring local address on private network")
            vm1.ssh(f"ifconfig eth{vif_1_1.param_get('device')} inet 169.254.1.1 broadcast 169.254.0.0 up")
            vm2.ssh(f"ifconfig eth{vif_2_1.param_get('device')} inet 169.254.2.1 broadcast 169.254.0.0 up")

            logging.info("Ping VMs")
            vm1.ssh("ping -c3 -w5 169.254.2.1")
            vm2.ssh("ping -c3 -w5 169.254.1.1")

        finally:
            # VIFs are destroyed by VM.destroy()
            if vm2 is not None:
                vm2.destroy()
            if vm1 is not None:
                vm1.destroy()
