from __future__ import annotations

import pytest

import logging

from lib.common import PackageManagerEnum, wait_for
from lib.host import Host
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

        pkg_mgr = vm.detect_package_manager()
        # RPM packages are built against Fedora 37 and won't install on
        # old RHEL-like distros (e.g., CentOS 7), so skip them.
        # The xen-guest-agent doesn't publish SUSE packages.
        if pkg_mgr not in (PackageManagerEnum.DNF, PackageManagerEnum.APT_GET):
            pytest.skip(f"Package manager '{pkg_mgr}' not supported in this test")

        # Remove conflicting xe-guest-utilities if present
        logging.info("Removing xe-guest-utilities if present")
        if pkg_mgr == PackageManagerEnum.DNF:
            vm.ssh('rpm -qa | grep xe-guest-utilities | xargs --no-run-if-empty rpm -e')
        elif pkg_mgr == PackageManagerEnum.APT_GET and \
                vm.ssh_with_result('dpkg -l xe-guest-utilities').returncode == 0:
            vm.ssh('apt-get remove -y xe-guest-utilities')

        if pkg_mgr == PackageManagerEnum.DNF:
            rpm_repo = xen_guest_agent_urls['rpm_repo']
            vm.ssh(f"echo -e '[xen-guest-agent]\\nbaseurl={rpm_repo}main/\\ngpgcheck=0'"
                   f" > /etc/yum.repos.d/xen-guest-agent.repo")
            vm.ssh('dnf install -y xen-guest-agent')
        elif pkg_mgr == PackageManagerEnum.APT_GET:
            # DEB packages are published to a stable APT repo in the GitLab
            # Generic Package Registry after each push to main.
            deb_repo = xen_guest_agent_urls['deb_repo']
            vm.ssh(f"echo 'deb [trusted=yes] {deb_repo} main/' "
                   f"> /etc/apt/sources.list.d/xen-guest-agent.list")
            vm.ssh('apt-get update')
            vm.ssh('apt-get install -y xen-guest-agent')

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
