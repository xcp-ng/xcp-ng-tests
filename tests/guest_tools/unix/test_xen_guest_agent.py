import pytest

import logging

from lib.common import PackageManagerEnum, wait_for

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.0
# From --vm parameter:
# - A Linux VM with systemd and a supported package manager (RPM or APT)


def _vif_published_ips(host, xs_prefix, vif_id, proto):
    """Return all IPs published under attr/vif/{vif_id}/{proto}/* in Xenstore."""
    ips = []
    for slot in range(10):  # NUM_IFACE_IPS = 10 in xen-guest-agent
        res = host.ssh_with_result(
            ['xenstore-read', f'{xs_prefix}/attr/vif/{vif_id}/{proto}/{slot}']
        )
        if res.returncode == 0:
            ips.append(res.stdout.strip())
    return ips


@pytest.mark.multi_vms
@pytest.mark.usefixtures("unix_vm")
class TestXenGuestAgent:
    @pytest.fixture(scope="class", autouse=True)
    def agent_install(self, running_vm, xen_guest_agent_packages):
        vm = running_vm

        if vm.ssh_with_result(['which', 'systemctl']).returncode != 0:
            pytest.skip("systemd not available on this VM")

        pkg_mgr = vm.detect_package_manager()
        if pkg_mgr not in (PackageManagerEnum.RPM, PackageManagerEnum.APT_GET):
            pytest.skip(f"Package manager '{pkg_mgr}' not supported in this test")

        # Remove conflicting xe-guest-utilities if present
        logging.info("Removing xe-guest-utilities if present")
        if pkg_mgr == PackageManagerEnum.RPM:
            vm.ssh('rpm -qa | grep xe-guest-utilities | xargs --no-run-if-empty rpm -e')
        if pkg_mgr == PackageManagerEnum.APT_GET and \
           vm.ssh_with_result(['dpkg', '-l', 'xe-guest-utilities']).returncode == 0:
            vm.ssh(['apt-get', 'remove', '-y', 'xe-guest-utilities'])

        # Copy package to VM and install
        if pkg_mgr == PackageManagerEnum.RPM:
            vm.scp(xen_guest_agent_packages['rpm'], '/root/xen-guest-agent.rpm')
            vm.ssh(['yum', 'install', '-y', '/root/xen-guest-agent.rpm'])
        if pkg_mgr == PackageManagerEnum.APT_GET:
            vm.scp(xen_guest_agent_packages['deb'], '/root/xen-guest-agent.deb')
            vm.ssh(['dpkg', '-i', '/root/xen-guest-agent.deb'])

        wait_for(
            lambda: vm.ssh_with_result(['systemctl', 'is-active', 'xen-guest-agent']).returncode == 0,
            "Wait for xen-guest-agent service to be active",
        )

    def test_agent_running(self, running_vm):
        running_vm.ssh(['systemctl', 'is-active', 'xen-guest-agent'])

    def test_agent_running_after_reboot(self, running_vm):
        running_vm.reboot(verify=True)
        running_vm.ssh(['systemctl', 'is-active', 'xen-guest-agent'])

    def test_xenstore_data(self, running_vm):
        vm = running_vm
        domid = vm.param_get('dom-id')
        host = vm.host
        xs_prefix = f'/local/domain/{domid}'

        logging.info("Check that xen-guest-agent published version info to Xenstore")
        host.ssh(['xenstore-read', f'{xs_prefix}/attr/PVAddons/MajorVersion'])
        host.ssh(['xenstore-read', f'{xs_prefix}/attr/PVAddons/BuildVersion'])

        logging.info("Check that OS info is published to Xenstore")
        host.ssh(['xenstore-read', f'{xs_prefix}/data/os_distro'])
        host.ssh(['xenstore-read', f'{xs_prefix}/data/os_uname'])

        logging.info("Check that memory info is published to Xenstore")
        host.ssh(['xenstore-read', f'{xs_prefix}/data/meminfo_total'])
        # meminfo_free is published on a 60s timer, wait for it to appear
        wait_for(
            lambda: host.ssh_with_result(['xenstore-read', f'{xs_prefix}/data/meminfo_free']).returncode == 0,
            "Wait for meminfo_free in Xenstore",
            timeout_secs=90,
        )

        logging.info("Check that the VM's IP is published under attr/vif")
        # VIF detection requires a Xen PV NIC; skip if none was detected
        if host.ssh_with_result(['xenstore-exists', f'{xs_prefix}/attr/vif']).returncode != 0:
            pytest.skip("No VIF published in Xenstore — VM may not be using a Xen PV NIC")

        ipv4s = _vif_published_ips(host, xs_prefix, vif_id=0, proto='ipv4')
        ipv6s = _vif_published_ips(host, xs_prefix, vif_id=0, proto='ipv6')
        logging.info("Published IPv4: %s, IPv6: %s", ipv4s, ipv6s)
        assert ipv4s or ipv6s, "No IPs published in Xenstore under attr/vif/0"
        assert vm.ip in ipv4s + ipv6s, \
            f"VM IP {vm.ip!r} not found in Xenstore (ipv4: {ipv4s}, ipv6: {ipv6s})"
