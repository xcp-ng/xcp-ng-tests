import pytest

import logging

from lib.common import Defer, wait_for
from lib.vif import VIF
from lib.vm import VM
from lib.windows import (
    vif_add_manual_configuration,
    vif_execute_powershell_script,
    vif_exists,
    vif_has_static_configuration,
    vif_uses_dhcp,
)

from typing import Generator

# Requirements:
# - Same as TestGuestToolsWindowsNondestructive.


@pytest.mark.multi_vms
@pytest.mark.usefixtures("windows_vm")
class TestVifConfigure:
    @pytest.fixture
    def temporary_vif(self, vm_install_test_tools_per_test_class: VM, defer: Defer) -> Generator[VIF, None, None]:
        vm = vm_install_test_tools_per_test_class
        existing_vifs = vm.vifs()
        network_uuid = existing_vifs[0].param_get("network-uuid")
        assert network_uuid is not None

        logging.info("Create temporary VIF")
        vif = vm.create_vif(1, network_uuid=network_uuid)
        defer(lambda: vif.destroy())
        vif.plug()
        defer(lambda: vif.unplug())

        wait_for(lambda: vif_exists(vif), "Wait for temporary VIF network adapter")
        # A static default route on this test-only interface must never take precedence over the management VIF.
        vif_execute_powershell_script(
            vif,
            r"""Set-NetIPInterface -InterfaceIndex $adapter.ifIndex `
-AddressFamily IPv4 `
-AutomaticMetric Disabled `
-InterfaceMetric 9999;
Set-NetIPInterface -InterfaceIndex $adapter.ifIndex `
-AddressFamily IPv6 `
-AutomaticMetric Disabled `
-InterfaceMetric 9999""",
        )
        yield vif

    def test_vif_configure_ipv4(self, temporary_vif: VIF) -> None:
        vif = temporary_vif
        address1 = "192.0.2.2"
        prefix = 24
        gateway1 = "192.0.2.1"
        address2 = "198.51.100.2"
        gateway2 = "198.51.100.1"

        logging.info("Configure DHCP IPv4")
        vif.configure_ipv4("dhcp")
        wait_for(lambda: vif_uses_dhcp(vif))

        logging.info("Configure static IPv4")
        vif.configure_ipv4("static", f"{address1}/{prefix}", gateway1)
        wait_for(lambda: vif_has_static_configuration(vif, "IPv4", address1, prefix, gateway1))

        logging.info("Configure noop IPv4")
        vif.configure_ipv4("none")
        wait_for(lambda: vif_has_static_configuration(vif, "IPv4", address1, prefix, gateway1))

        logging.info("Reconfigure static IPv4")
        vif.configure_ipv4("static", f"{address2}/{prefix}", gateway2)
        wait_for(lambda: vif_has_static_configuration(vif, "IPv4", address1, prefix, gateway1, present=False))
        wait_for(lambda: vif_has_static_configuration(vif, "IPv4", address2, prefix, gateway2))

        logging.info("Configure DHCP IPv4")
        vif.configure_ipv4("dhcp")
        wait_for(lambda: vif_uses_dhcp(vif))
        wait_for(lambda: vif_has_static_configuration(vif, "IPv4", address2, prefix, gateway2, present=False))

    def test_vif_configure_ipv4_dhcp_to_static(self, temporary_vif: VIF) -> None:
        vif = temporary_vif
        address = "203.0.113.2"
        prefix = 24
        gateway = "203.0.113.1"

        logging.info("Configure DHCP IPv4")
        vif.configure_ipv4("dhcp")
        wait_for(lambda: vif_uses_dhcp(vif))

        logging.info("Add manual configuration")
        vif_add_manual_configuration(vif, address, prefix, gateway)
        wait_for(lambda: vif_has_static_configuration(vif, "IPv4", address, prefix, gateway))

        logging.info("Configure static IPv4 while keeping address %s/%s and gateway %s", address, prefix, gateway)
        vif.configure_ipv4("static", f"{address}/{prefix}", gateway)
        wait_for(lambda: vif_has_static_configuration(vif, "IPv4", address, prefix, gateway))

    def test_vif_configure_ipv6(self, temporary_vif: VIF) -> None:
        vif = temporary_vif
        address1 = "2001:db8:1::2"
        prefix = 64
        gateway1 = "2001:db8:1::1"
        address2 = "2001:db8:2::3"
        gateway2 = "2001:db8:2::254"

        logging.info("Configure autoconf IPv6")
        vif.configure_ipv6("autoconf")

        logging.info("Configure static IPv6")
        vif.configure_ipv6("static", f"{address1}/{prefix}", gateway1)
        wait_for(lambda: vif_has_static_configuration(vif, "IPv6", address1, prefix, gateway1))

        logging.info("Reconfigure noop IPv6")
        vif.configure_ipv6("none")
        wait_for(lambda: vif_has_static_configuration(vif, "IPv6", address1, prefix, gateway1))

        logging.info("Reconfigure static IPv6")
        vif.configure_ipv6("static", f"{address2}/{prefix}", gateway2)
        wait_for(lambda: vif_has_static_configuration(vif, "IPv6", address1, prefix, gateway1, present=False))
        wait_for(lambda: vif_has_static_configuration(vif, "IPv6", address2, prefix, gateway2))

        logging.info("Configure autoconf IPv6")
        vif.configure_ipv6("autoconf")
        wait_for(lambda: vif_has_static_configuration(vif, "IPv6", address2, prefix, gateway2, present=False))

    def test_vif_configure_ipv6_autoconf_to_static(self, temporary_vif: VIF) -> None:
        vif = temporary_vif
        address = "2001:db8:3::2"
        prefix = 64
        gateway = "2001:db8:3::1"

        logging.info("Configure autoconf IPv6")
        vif.configure_ipv6("autoconf")

        logging.info("Add manual configuration")
        vif_add_manual_configuration(vif, address, prefix, gateway)
        wait_for(lambda: vif_has_static_configuration(vif, "IPv6", address, prefix, gateway))

        logging.info(
            "Configure static IPv6 while keeping address %s/%s and gateway %s",
            address,
            prefix,
            gateway,
        )
        vif.configure_ipv6("static", f"{address}/{prefix}", gateway)
        wait_for(lambda: vif_has_static_configuration(vif, "IPv6", address, prefix, gateway))
