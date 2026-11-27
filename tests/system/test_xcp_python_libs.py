import pytest

import shlex

from lib.host import Host
from pkgfixtures import host_with_saved_yum_state  # noqa: F401

from typing import Generator

# Tests for the python3-xcp-libs package, specifically the updategrub.py script
# installed at /opt/xensource/bin/updategrub.py.
#
# The script operates on the real /boot/grub/grub.cfg.  Each test that
# modifies the file uses the grub_backup fixture to restore the original
# afterwards.
#
# Requirements:
# - an XCP-ng host (--hosts) running default kernel from "kernel" package

UPDATEGRUB = '/opt/xensource/bin/updategrub.py'
GRUB_CFG = '/boot/grub/grub.cfg'


def _tell_kernel_version_from_package(host: Host, package: str = "kernel") -> str | None:
    """
    Return the KVER string for *package* by querying the RPM database,
    or None when the package is not installed.

    The KVER is extracted from the /boot/config-<kver> file that the package
    installs, without assuming any particular suffix convention.
    """
    result = host.ssh_with_result(f"rpm -ql {package}")
    if result.returncode != 0:
        return None
    for path in result.stdout.splitlines():
        name = path.strip().rsplit('/', 1)[-1]
        if name.startswith('config-'):
            return name.removeprefix('config-')
    return None


def _updategrub(host: Host, action: str, flavour: str, version: str) -> str:
    """Run updategrub.py with positional arguments and return stdout."""
    return host.ssh(f"{UPDATEGRUB} {action} {shlex.quote(flavour)} {shlex.quote(version)}")


@pytest.fixture(scope="module", autouse=True)
def host_with_updategrub(host: Host) -> None:
    """Fail fast if updategrub.py is absent or not owned by python3-xcp-libs."""
    if not host.file_exists(UPDATEGRUB):
        pytest.fail(f"{UPDATEGRUB} is not present on the host – is python3-xcp-libs installed?")
    result = host.ssh_with_result(f"rpm -qf {UPDATEGRUB} --qf '%{{NAME}}'")
    if result.returncode != 0 or result.stdout.strip() != "python3-xcp-libs":
        pytest.fail(
            f"{UPDATEGRUB} is not owned by python3-xcp-libs "
            f"(rpm -qf reports: {result.stdout.strip()!r})"
        )


@pytest.fixture(scope="module")
def host_with_default_kernel(host: Host) -> Generator[tuple[Host, str], None, None]:
    """
    Yield (host, version) where version is the KVER of the currently running kernel.

    Skips if the running kernel does not match the installed kernel package
    (e.g. the host was updated without a reboot, or is currently on kernel-alt),
    because the test relies on starting from the main kernel.

    At teardown, assert the host is still running that same kernel.
    """
    version = host.ssh("uname -r")
    pkg_version = _tell_kernel_version_from_package(host, "kernel")
    if pkg_version is None:
        pytest.fail("kernel package not found on host")
    if version != pkg_version:
        pytest.skip(
            f"Running kernel {version!r} does not match installed kernel package "
            f"{pkg_version!r}; host must be booted on the main kernel to run this test"
        )
    yield host, version
    running = host.ssh("uname -r")
    assert running == version, (
        f"Host kernel changed: started on {version!r}, now running {running!r}"
    )


@pytest.fixture(scope="function")
def host_with_alt_kernel_installed(
    host_with_saved_yum_state: Host,
) -> Generator[tuple[Host, str], None, None]:
    """
    Ensure kernel-alt is installed and yield (host, kalt_ver).

    If kernel-alt is not already present it is installed first.
    The yum state is saved before setup and fully restored at teardown by
    host_with_saved_yum_state, so no manual install/remove bookkeeping is needed.
    """
    host = host_with_saved_yum_state
    if not host.is_package_installed('kernel-alt'):
        host.yum_install(['kernel-alt'])
    kalt_ver = _tell_kernel_version_from_package(host, "kernel-alt")
    assert kalt_ver is not None, "No kernel-alt config file found after installation"
    yield host, kalt_ver


@pytest.fixture(scope="function")
def grub_backup(host: Host) -> Generator[None, None, None]:
    """
    Back up the real grub.cfg before each test and restore it afterwards,
    so the host bootloader is always left in its original state.
    """
    backup = host.ssh("mktemp")
    host.ssh(f"cp {GRUB_CFG} {backup}")
    try:
        yield
    finally:
        host.ssh(f"cp {backup} {GRUB_CFG}", check=False)
        host.ssh(f"rm -f {backup}", check=False)


class TestUpdategrubSetDefault:
    """
    End-to-end tests for the 'set-default' action of updategrub.py.

    test_set_default_switches_boot_kernel installs kernel-alt, verifies
    that it doesn't boot by default, then uses set-default to switch between
    kernel-alt and the default kernel across three reboots.
    """

    @pytest.mark.reboot
    def test_set_default_switches_boot_kernel(
        self,
        grub_backup: None,
        host_with_default_kernel: tuple[Host, str],
        host_with_alt_kernel_installed: tuple[Host, str],
    ) -> None:
        """
        1. kernel-alt is installed by the host_with_alt_kernel_installed fixture; its
           %post scriptlet calls `updategrub.py add` automatically, adding a
           grub entry without changing the default.
        2. Reboot → still boots the default kernel (default unchanged).
        3. `updategrub.py set-default kernel-alt <ver>` → reboot → kernel-alt boots.
        4. `updategrub.py set-default kernel <ver>` → reboot → default kernel boots again.

        grub.cfg is restored by grub_backup at teardown.
        kernel-alt is removed by host_with_alt_kernel_installed at teardown.
        """
        host, k_ver = host_with_default_kernel
        _, kalt_ver = host_with_alt_kernel_installed

        # The %post scriptlet must have added the grub entry already.
        assert f"XCP-ng kernel-alt {kalt_ver}" in host.ssh(f"cat {GRUB_CFG}"), (
            "Expected kernel-alt grub entry to be added by the package scriptlet"
        )

        # --- reboot 1: default unchanged → still boots default kernel ---
        host.reboot(verify=True)
        running = host.ssh("uname -r")
        assert running == k_ver, (
            f"Expected default kernel {k_ver!r} after install without set-default, "
            f"got {running!r}"
        )

        # --- set kernel-alt as default, reboot 2 → kernel-alt boots ---
        _updategrub(host, "set-default", "kernel-alt", kalt_ver)
        host.reboot(verify=True)
        running = host.ssh("uname -r")
        assert running == kalt_ver, (
            f"Expected kernel-alt {kalt_ver!r} after 'set-default kernel-alt' + reboot, "
            f"got {running!r}"
        )

        # --- restore default kernel, reboot 3 → default kernel boots ---
        _updategrub(host, "set-default", "kernel", k_ver)
        host.reboot(verify=True)
        running = host.ssh("uname -r")
        assert running == k_ver, (
            f"Expected default kernel {k_ver!r} after restoring default, "
            f"got {running!r}"
        )
