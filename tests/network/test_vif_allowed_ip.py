import ipaddress
import os
import pytest

# Requirements:
# - one XCP-ng host (--host) >= 8.2 (>= 8.3 for the CIDR tests) with no SDN controller configured
# - a VM (--vm)

def ip_responsive(ip):
    return not os.system(f"ping -c 3 -W 10 {ip} > /dev/null 2>&1")

@pytest.mark.small_vm
@pytest.mark.usefixtures("host_no_sdn_controller")
class TestAllowedIP:
    def test_unallowed_ip(self, running_vm):
        vm = running_vm
        vif = vm.vifs()[0]
        ip = vm.ip
        ip_address = ipaddress.ip_address(ip)
        ip_family = ip_address.version
        dummy_ip = str(ip_address + 1)
        assert ip_responsive(ip)

        vif.param_add(f"ipv{ip_family}-allowed", dummy_ip)
        vif.param_set("locking-mode", "locked")
        assert not ip_responsive(ip)

        vif.param_clear(f"ipv{ip_family}-allowed")
        vif.param_set("locking-mode", "unlocked")

    def test_allowed_ip(self, running_vm):
        vm = running_vm
        vif = vm.vifs()[0]
        ip = vm.ip
        ip_address = ipaddress.ip_address(ip)
        ip_family = ip_address.version

        vif.param_clear(f"ipv{ip_family}-allowed")
        vif.param_set("locking-mode", "locked")
        assert not ip_responsive(ip)

        vif.param_add(f"ipv{ip_family}-allowed", ip)
        assert ip_responsive(ip)

        vif.param_remove(f"ipv{ip_family}-allowed", ip)
        assert not ip_responsive(ip)

        vif.param_set("locking-mode", "unlocked")

@pytest.mark.small_vm
@pytest.mark.usefixtures("host_at_least_8_3", "host_no_sdn_controller")
class TestAllowedCIDR:
    def test_unallowed_cidr(self, running_vm):
        vm = running_vm
        vif = vm.vifs()[0]
        ip = vm.ip
        ip_address = ipaddress.ip_address(ip)
        ip_family = ip_address.version
        lo = "127.0.0.1" if ip_family == 4 else "::1"
        dummy_cidr = f'{lo}/24'
        assert ip_responsive(ip)

        vif.param_add(f"ipv{ip_family}-allowed", dummy_cidr)
        vif.param_set("locking-mode", "locked")
        assert not ip_responsive(ip)

        vif.param_clear(f"ipv{ip_family}-allowed")
        vif.param_set("locking-mode", "unlocked")

    def test_allowed_cidr(self, running_vm):
        vm = running_vm
        vif = vm.vifs()[0]
        ip = vm.ip
        ip_address = ipaddress.ip_address(ip)
        ip_family = ip_address.version
        cidr = f'{ip}/24'

        vif.param_clear(f"ipv{ip_family}-allowed")
        vif.param_set("locking-mode", "locked")
        assert not ip_responsive(ip)

        vif.param_add(f"ipv{ip_family}-allowed", cidr)
        assert ip_responsive(ip)

        vif.param_remove(f"ipv{ip_family}-allowed", cidr)
        assert not ip_responsive(ip)

        vif.param_set("locking-mode", "unlocked")
