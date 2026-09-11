from __future__ import annotations

import logging
from enum import Enum, auto

from lib.commands import SSHResult

from typing import TYPE_CHECKING, Protocol, TypeAlias

if TYPE_CHECKING:
    from lib.host import Host
    from lib.vm import VM

class RemoteDryRun:
    def ssh(self, cmd: str, *args, **kargs) -> None:
        assert False, "RemoteDryRun.ssh shouldn't be called"

    def ssh_with_result(self, cmd: str, *args, **kargs) -> SSHResult:
        assert False, "RemoteDryRun.ssh_with_result shouldn't be called"

    def file_exists(self, filepath: str) -> bool:
        assert False, "RemoteDryRun.file_exists shouldn't be called"

if TYPE_CHECKING:
    Sshable: TypeAlias = Host | VM | RemoteDryRun

# --------------------------------------------------------------------
def all_packagemanagers() -> list[type[PackageManager]]:
    """
    Return the list of all PackageManager declared.
    """
    # the list's order is important
    # it is used by PackageManager.detect(): the first match wins.
    return [
        XCPng9PackageManager,
        XCPng8PackageManager,
        DnfPackageManager,
        YumPackageManager,
        AptGetPackageManager,
        ApkPackageManager,
        ZypperPackageManager,
    ]

# --------------------------------------------------------------------
class Package(Enum):
    e2fsprogs = auto()
    fio = auto()
    iperf3 = auto()
    tcpdump = auto()
    tpm2_tools = auto()
    util_linux = auto()

    @staticmethod
    def all() -> list[Package]:
        return [Package[p] for p in Package.__members__]

# --------------------------------------------------------------------
class PackageManager(Protocol):
    # remote endpoint to manage
    remote: Sshable

    # conversion table for Package -> package_name (generic name to local package name)
    # could be None if the package isn't present and can't be installed
    packages: dict[Package, str | None]

    # list of essentials packages (packages that can't be uninstalled)
    essentials: list[Package] = []

    @staticmethod
    def has_packagemanager(remote: Sshable) -> bool:
        """
        Return if the remote could be managed using the package manager.
        """
        ...

    def is_installed_raw(self, package_name: str) -> bool:
        """
        Return if a package (by using the raw package name) is installed on the system.
        """
        ...

    def install_raw(self, package_name: str) -> None:
        """
        Install a package (by raw by using the raw package name on the system.
        """
        ...

    def uninstall_raw(self, package_name: str) -> None:
        """
        Uninstall a package (by using the raw package name) from the system.
        """
        ...

    @staticmethod
    def detect(remote: Sshable) -> PackageManager:
        for PM in all_packagemanagers():
            if PM.has_packagemanager(remote):
                return PM(remote)

        raise NotImplementedError("Failed to detect suitable PackageManger")

    def __init__(self, remote: Sshable):
        self.remote = remote
        self.assert_all_packages_declared()

    def name(self):
        """
        Return the name of the PackageManager
        """
        return self.__class__.__name__

    def package_name(self, package: Package) -> str:
        """
        Convert a normalized package to system dependent package name. May raise exception.
        """
        name = self.packages.get(package)
        if name is None:
            raise NotImplementedError(f"Package '{package}' not found in packages definition of {self.name()}")
        return name

    def assert_all_packages_declared(self) -> None:
        all_packages = Package.all()

        assert len(self.packages) == len(all_packages)
        for package in all_packages:
            assert package in self.packages, f"package '{package.name}' not present in {self.name()}"

    def is_installable(self, package: Package) -> bool:
        """
        Return if a package is installable using the PackageManager.
        """
        return self.packages.get(package) is not None

    def is_installed(self, package: Package) -> bool:
        """
        Return if a package is installed on the system.
        """
        name = self.package_name(package)
        return self.is_installed_raw(name)

    def install(self, package: Package) -> None:
        """
        Ensure a package is installed on the system.
        """
        name = self.package_name(package)
        if not self.is_installed_raw(name):
            logging.info(f"{self.name()}: installing {package}")
            self.install_raw(name)
        else:
            logging.debug(f"{self.name()}: installing {package} (already installed)")

    def uninstall(self, package: Package) -> None:
        """
        Ensure a package is not installed on the system.
        """
        if package in self.essentials:
            logging.debug(f"{self.name()}: uninstalling {package} (skipped, essential package)")
            return

        name = self.package_name(package)
        if self.is_installed_raw(name):
            logging.info(f"{self.name()}: uninstalling {package}")
            self.uninstall_raw(name)
        else:
            logging.debug(f"{self.name()}: uninstalling {package} (already uninstalled)")

    def installed_state(self, package: Package, state: bool | None = None) -> bool:
        """
        Idempotent action to get/set a package status (if installed or not).
        """
        package_name = self.package_name(package)
        previous = self.is_installed_raw(package_name)

        if state is not None:
            if state != previous:
                if state:
                    logging.info(f"{self.name()}: installing {package}")
                    self.install_raw(package_name)
                else:
                    logging.info(f"{self.name()}: uninstalling {package}")
                    self.uninstall_raw(package_name)
            else:
                logging.info(
                    f"{self.name()}: {package} already in wanted state ({'installed' if state else 'uninstalled'})"
                )

        return previous

# --------------------------------------------------------------------

# https://wiki.alpinelinux.org/wiki/Alpine_Package_Keeper
class ApkPackageManager(PackageManager):
    packages = {
        Package.e2fsprogs: "e2fsprogs-extra",
        Package.fio: "fio",
        Package.iperf3: "iperf3",
        Package.tcpdump: "tcpdump",
        Package.tpm2_tools: None,
        Package.util_linux: "util-linux",
    }

    @staticmethod
    def has_packagemanager(remote: Sshable) -> bool:
        return remote.file_exists('/sbin/apk')

    def is_installed_raw(self, package_name: str) -> bool:
        return self.remote.ssh_with_result(f"grep -q ^{package_name}$ /etc/apk/world").returncode == 0

    def install_raw(self, package_name: str) -> None:
        self.remote.ssh(f"apk add --force-refresh {package_name}")

    def uninstall_raw(self, package_name: str) -> None:
        self.remote.ssh(f"apk del {package_name}")


# https://manpages.debian.org/buster/apt/apt-get.8.en.html
class AptGetPackageManager(PackageManager):
    packages = {
        Package.e2fsprogs: "e2fsprogs",
        Package.fio: "fio",
        Package.iperf3: "iperf3",
        Package.tcpdump: "tcpdump",
        Package.tpm2_tools: "tpm2-tools",
        Package.util_linux: "util-linux",
    }
    essentials = [
        Package.e2fsprogs,
        Package.util_linux,
    ]

    @staticmethod
    def has_packagemanager(remote: Sshable) -> bool:
        return remote.file_exists('/usr/bin/apt-get')

    def is_installed_raw(self, package_name: str) -> bool:
        return self.remote.ssh_with_result(f"dpkg -l -- {package_name}").returncode == 0

    def install_raw(self, package_name: str) -> None:
        self.remote.ssh(f"apt-get update && apt-get install -y -qq -- {package_name}")

    def uninstall_raw(self, package_name: str) -> None:
        self.remote.ssh(f"apt-get purge -y -- {package_name}")


# https://dnf.readthedocs.io/en/latest/command_ref.html
class DnfPackageManager(PackageManager):
    packages = {
        Package.e2fsprogs: "e2fsprogs",
        Package.fio: "fio",
        Package.iperf3: "iperf3",
        Package.tcpdump: "tcpdump",
        Package.tpm2_tools: "tpm2-tools",
        Package.util_linux: "util-linux",
    }
    essentials = [
        Package.util_linux,
    ]

    @staticmethod
    def has_packagemanager(remote: Sshable) -> bool:
        return remote.file_exists('/usr/bin/dnf')

    def is_installed_raw(self, package_name: str) -> bool:
        return self.remote.ssh_with_result(f"rpm -q -- {package_name}").returncode == 0

    def install_raw(self, package_name: str) -> None:
        self.remote.ssh(f"dnf install -y -- {package_name}")

    def uninstall_raw(self, package_name: str) -> None:
        self.remote.ssh(f"dnf remove -y -- {package_name}")


# http://yum.baseurl.org/
class YumPackageManager(PackageManager):
    packages = {
        Package.e2fsprogs: "e2fsprogs",
        Package.fio: "fio",
        Package.iperf3: "iperf3",
        Package.tcpdump: "tcpdump",
        Package.tpm2_tools: "tpm2-tools",
        Package.util_linux: "util-linux",
    }
    essentials = [
        Package.util_linux,
    ]

    @staticmethod
    def has_packagemanager(remote: Sshable) -> bool:
        return remote.file_exists('/usr/bin/yum')

    def is_installed_raw(self, package_name: str) -> bool:
        return self.remote.ssh_with_result(f"rpm -q -- {package_name}").returncode == 0

    def install_raw(self, package_name: str) -> None:
        self.remote.ssh(f"yum install -y -- {package_name}")

    def uninstall_raw(self, package_name: str) -> None:
        self.remote.ssh(f"yum remove -y -- {package_name}")


# XCP-ng 8.2/8.3 is based on YumPackageManager
# (some packages aren't available)
class XCPng8PackageManager(YumPackageManager):
    packages = {
        Package.e2fsprogs: "e2fsprogs",
        Package.fio: None,
        Package.iperf3: "iperf3",
        Package.tcpdump: "tcpdump",
        Package.tpm2_tools: None,
        Package.util_linux: "util-linux",
    }

    @staticmethod
    def has_packagemanager(remote: Sshable) -> bool:
        return YumPackageManager.has_packagemanager(remote) and remote.file_exists('/etc/yum.repos.d/xcp-ng.repo')


# XCP-ng 9 is based on DnfPackageManager
# (some packages aren't available)
class XCPng9PackageManager(DnfPackageManager):
    packages = {
        Package.e2fsprogs: "e2fsprogs",
        Package.fio: None,
        Package.iperf3: "iperf3",
        Package.tcpdump: "tcpdump",
        Package.tpm2_tools: None,
        Package.util_linux: "util-linux",
    }

    @staticmethod
    def has_packagemanager(remote: Sshable) -> bool:
        return DnfPackageManager.has_packagemanager(remote) and remote.file_exists('/etc/yum.repos.d/xcp-ng.repo')


# https://manpages.opensuse.org/Tumbleweed/zypper/zypper.8.en.html
class ZypperPackageManager(PackageManager):
    packages = {
        Package.e2fsprogs: "e2fsprogs",
        Package.fio: "fio",
        Package.iperf3: "iperf",
        Package.tcpdump: "tcpdump",
        Package.tpm2_tools: None,
        Package.util_linux: "util-linux",
    }
    essentials = [
        Package.util_linux,
    ]

    @staticmethod
    def has_packagemanager(remote: Sshable) -> bool:
        return remote.file_exists('/usr/bin/zypper')

    def is_installed_raw(self, package_name: str) -> bool:
        return self.remote.ssh_with_result(
            f"zypper search --installed-only --match-exact -- {package_name}"
        ).returncode == 0

    def install_raw(self, package_name: str) -> None:
        self.remote.ssh(f"zypper --non-interactive install -- {package_name}")

    def uninstall_raw(self, package_name: str) -> None:
        self.remote.ssh(f"zypper --non-interactive remove -- {package_name}")
