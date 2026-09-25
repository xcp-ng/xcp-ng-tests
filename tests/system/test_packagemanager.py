from __future__ import annotations

import pytest

import logging

try:
    from vm_data import VMS
except ImportError:
    VMS = {}

from data import CACHE_IMPORTED_VM
from lib import packagemanager
from lib.common import Defer
from lib.host import Host
from lib.packagemanager import Package, PackageManager, RemoteDryRun, all_packagemanagers

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.0

def do_check_essentials(pkg_mgr: PackageManager):
    """Check all declared essentials packages are effectively installed."""
    for package in pkg_mgr.essentials:
        if not pkg_mgr.is_installed(package):
            pytest.fail(f"unexpected essential package {package}, it isn't installed")


def do_install_packages(defer: Defer, pkg_mgr: PackageManager):
    """Try to install/uninstall each declared packages."""
    for package in Package.all():
        if not pkg_mgr.is_installable(package):
            continue

        state = pkg_mgr.installed_state(package, True)
        defer(lambda package=package, state=state: pkg_mgr.installed_state(package, state)) # type: ignore[misc]

        if not pkg_mgr.is_installed(package):
            pytest.fail(f"package '{package}' is not installed after installation")

        # check install could be called twice in a row
        pkg_mgr.install(package)

        # check if package is properly installed
        if package == Package.tcpdump:
            pkg_mgr.remote.ssh("command -v tcpdump")

        pkg_mgr.uninstall(package)
        if pkg_mgr.is_installed(package) and package not in pkg_mgr.essentials:
            pytest.fail(f"package '{package}' is still installed after removal")

        # check uninstall could be called twice in a row
        pkg_mgr.uninstall(package)


def pm_url() -> list[tuple[type[PackageManager], str | None]]:
    """Return a list (PM, vm_url) suitable for @pytest.mark.parametrize()."""
    checked_packagemanagers: dict[type[PackageManager], str | None] = {PM: None for PM in all_packagemanagers()}

    # get VM URL from VMS
    for entry in VMS.get('multi', {}).get('all_unix', []):
        # get url part
        if type(entry) is tuple:
            vm_url = entry[0]
        elif type(entry) is str:
            vm_url = entry
        else:
            continue

        # check name for know parts
        PM: type[PackageManager] | None = None

        if "alpine" in vm_url:
            PM = packagemanager.ApkPackageManager
        elif "debian-12" in vm_url:
            PM = packagemanager.AptGetPackageManager
        elif "almalinux-9" in vm_url:
            PM = packagemanager.DnfPackageManager
        elif "openSUSE" in vm_url:
            PM = packagemanager.ZypperPackageManager

        # keep only the first matching url
        if PM is not None and checked_packagemanagers.get(PM) is None:
            checked_packagemanagers[PM] = vm_url

    return list(checked_packagemanagers.items())


class TestPackageManager:

    @pytest.mark.no_vm
    def test_host(self, host: Host, defer: Defer):
        """Check PackageManager on the host."""
        pkg_mgr = host.package_manager()
        do_check_essentials(pkg_mgr)
        do_install_packages(defer, pkg_mgr)

    @pytest.mark.no_vm
    def test_packages_coverage(self):
        """Check all packages are declared on all PackageManager."""
        remote = RemoteDryRun()
        for PM in all_packagemanagers():
            pm = PM(remote)
            pm.assert_all_packages_declared()

    @pytest.mark.parametrize("PM, vm_url", pm_url())
    def test_vm(self, host: Host, defer: Defer, PM: type[PackageManager], vm_url: str | None):
        """Check that all packages are installable."""
        if vm_url is None:
            if PM in [
                packagemanager.XCPng8PackageManager,
                packagemanager.XCPng9PackageManager,
            ]:
                pytest.skip(f"{PM.__name__}: covered by test_host()")

            # TBD
            if PM in [
                packagemanager.YumPackageManager,
            ]:
                pytest.skip(f"{PM.__name__}: PM needs a VM with reachable yum repository")

            pytest.fail(f"No vm_url for {PM.__name__} found in vm_data.py")

        logging.info(f"Checking {PM.__name__} with '{vm_url}'")

        vm = host.import_vm(vm_url, host.main_sr_uuid(), use_cache=CACHE_IMPORTED_VM)
        if CACHE_IMPORTED_VM:
            vm = vm.clone()
        defer(lambda: vm.destroy())

        vm.start()
        vm.wait_for_vm_running_and_ssh_up()

        pkg_mgr = vm.package_manager()

        # check that pm_url fixture is right
        if not isinstance(pkg_mgr, PM):
            pytest.fail(f"unexpected PackageManager instance for the provided url (found '{pkg_mgr.name()}')")

        do_check_essentials(pkg_mgr)
        do_install_packages(defer, pkg_mgr)
