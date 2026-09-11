from __future__ import annotations

import pytest

import logging

from lib.common import wait_for
from lib.host import Host
from lib.packagemanager import AptGetPackageManager, DnfPackageManager
from lib.vm import VM

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.0
# From --vm parameter:
# - A Linux VM with systemd and a supported package manager (DNF or APT)


def _vif_published_ips(host: Host, xs_prefix: str, vif_id: int, proto: str) -> list[str]:
    """Return all IPs published under attr/vif/{vif_id}/{proto}/* in Xenstore."""
    parent = f'{xs_prefix}/attr/vif/{vif_id}/{proto}'
    res = host.ssh_with_result(f'xenstore-list {parent}')
    if res.returncode != 0:
        return []
    return [
        host.ssh(f'xenstore-read {parent}/{slot}').strip()
        for slot in res.stdout.split()
    ]


@pytest.mark.skip(reason="Test suite is currently unstable — skipped until fixed")
@pytest.mark.multi_vms
@pytest.mark.usefixtures("unix_vm")
class TestXenGuestAgent:
    @pytest.fixture(scope="class", autouse=True)
    def agent_install(self, running_vm: VM, xen_guest_agent_urls: dict[str, str]) -> None:
        vm = running_vm

        if vm.ssh_with_result('which systemctl').returncode != 0:
            pytest.skip("systemd not available on this VM")

        pkg_mgr = vm.package_manager()

        # RPM packages are built against Fedora 37 and won't install on
        # old RHEL-like distros (e.g., CentOS 7), so skip them.
        # The xen-guest-agent doesn't publish SUSE packages.
        if not isinstance(pkg_mgr, DnfPackageManager | AptGetPackageManager):
            pytest.skip(f"Package manager '{pkg_mgr.name()}' not supported in this test")

        # Remove conflicting xe-guest-utilities if present
        logging.info("Removing xe-guest-utilities if present")
        if isinstance(pkg_mgr, DnfPackageManager):
            vm.ssh('rpm -qa | grep xe-guest-utilities | xargs --no-run-if-empty rpm -e')
        elif isinstance(pkg_mgr, AptGetPackageManager) and \
                pkg_mgr.is_installed_raw('xe-guest-utilities'):
            pkg_mgr.uninstall_raw('xe-guest-utilities')

        if isinstance(pkg_mgr, DnfPackageManager):
            rpm_repo = xen_guest_agent_urls['rpm_repo']
            vm.create_file(
                "/etc/yum.repos.d/xen-guest-agent.repo",
                f"""[xen-guest-agent]
baseurl={rpm_repo}main/
gpgcheck=0""",
            )
            pkg_mgr.install_raw('xen-guest-agent')

        elif isinstance(pkg_mgr, AptGetPackageManager):
            # DEB packages are published to a stable APT repo in the GitLab
            # Generic Package Registry after each push to main.
            deb_repo = xen_guest_agent_urls['deb_repo']
            vm.create_file(
                "/etc/apt/sources.list.d/xen-guest-agent.list",
                f"deb [trusted=yes] {deb_repo} main/",
            )
            pkg_mgr.install_raw('xen-guest-agent')

        wait_for(
            lambda: vm.ssh_with_result('systemctl is-active xen-guest-agent').returncode == 0,
            "Wait for xen-guest-agent service to be active",
        )

    def test_agent_running_after_reboot(self, running_vm: VM) -> None:
        running_vm.reboot(verify=True)
        running_vm.ssh('systemctl is-active xen-guest-agent')

    def test_xenstore_version(self, running_vm: VM) -> None:
        host = running_vm.host
        xs_prefix = f'/local/domain/{running_vm.param_get("dom-id")}'
        host.ssh(f'xenstore-read {xs_prefix}/attr/PVAddons/MajorVersion')
        host.ssh(f'xenstore-read {xs_prefix}/attr/PVAddons/BuildVersion')

    def test_xenstore_os_info(self, running_vm: VM) -> None:
        host = running_vm.host
        xs_prefix = f'/local/domain/{running_vm.param_get("dom-id")}'
        host.ssh(f'xenstore-read {xs_prefix}/data/os_distro')
        host.ssh(f'xenstore-read {xs_prefix}/data/os_uname')

    def test_xenstore_memory(self, running_vm: VM) -> None:
        host = running_vm.host
        xs_prefix = f'/local/domain/{running_vm.param_get("dom-id")}'
        host.ssh(f'xenstore-read {xs_prefix}/data/meminfo_total')
        # meminfo_free is published on a 60s timer, wait for it to appear
        wait_for(
            lambda: host.ssh_with_result(f'xenstore-read {xs_prefix}/data/meminfo_free').returncode == 0,
            "Wait for meminfo_free in Xenstore",
            timeout_secs=90,
        )

    def test_xenstore_feature_balloon(self, running_vm: VM) -> None:
        host = running_vm.host
        xs_prefix = f'/local/domain/{running_vm.param_get("dom-id")}'
        res = host.ssh_with_result(f'xenstore-read {xs_prefix}/control/feature-balloon')
        if res.returncode != 0:
            pytest.skip("control/feature-balloon not present — agent may lack write permission on this host")
        assert res.stdout.strip() == '1', \
            f"Expected control/feature-balloon to be '1', got {res.stdout.strip()!r}"

    def test_xenstore_vif_ip(self, running_vm: VM) -> None:
        vm = running_vm
        host = vm.host
        xs_prefix = f'/local/domain/{vm.param_get("dom-id")}'
        if host.ssh_with_result(f'xenstore-exists {xs_prefix}/attr/vif').returncode != 0:
            pytest.skip("No VIF published in Xenstore — VM may not be using a Xen PV NIC")
        ipv4s = _vif_published_ips(host, xs_prefix, vif_id=0, proto='ipv4')
        ipv6s = _vif_published_ips(host, xs_prefix, vif_id=0, proto='ipv6')
        logging.info("Published IPv4: %s, IPv6: %s", ipv4s, ipv6s)
        assert ipv4s or ipv6s, "No IPs published in Xenstore under attr/vif/0"
        assert vm.ip in ipv4s + ipv6s, \
            f"VM IP {vm.ip!r} not found in Xenstore (ipv4: {ipv4s}, ipv6: {ipv6s})"
