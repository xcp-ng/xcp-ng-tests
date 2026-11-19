import pytest

import logging
import re

from lib.host import Host

# Requirements:
# - an XCP-ng host (--hosts) >= 8.3, with `kexec-tools` and
#   `xen-crashdump-analyser` installed, booted with the Xen argument
#   `crashkernel=` (default).
# - TestKdumpCrash crashes the host on purpose. The host must be nested. It is
#   marked with `host_crash` and is only selected by the `host-crash` job in
#   jobs.py, which is meant to run against a nested host. It refuses (fails)
#   to run on a host that is not nested.
#
# What this covers:
#   The crash dump path has three parts:
#     1. kdump.service (xs-kdump, from kexec-tools) loads a crashed kernel into
#        Xen at boot, with `init=/usr/sbin/kdump` and the Xen and dom0
#        versions on its command line, after staging the Xen and dom0 symbol
#        files in /var/lib/kdump and reserving some disk space in
#        CRASH_LOG_DIR.
#     2. When Xen or dom0 crashes, Xen jumps into that crashed kernel, which
#        runs /usr/sbin/kdump instead of a normal init.
#     3. /usr/sbin/kdump runs `xen-crashdump-analyser` on /proc/vmcore, writes
#        its logs to a timestamped directory under CRASH_LOG_DIR and reboots.
#   TestKdumpSetup checks 1 without disturbing the host. TestKdumpCrash checks
#   the whole chain with a real hypervisor crash.

KDUMP_SYSCONFIG = "/etc/sysconfig/kdump"


def _kdump_conf(host: Host, name: str) -> str:
    """Return a kdump setting as xs-kdump and kdump see it."""
    return host.ssh(f"bash -c '. {KDUMP_SYSCONFIG}; echo \"${{{name}}}\"'")


def _crashed_kernel_loaded(host: Host) -> bool:
    """Ask Xen whether a crashed kernel is loaded."""
    # `kexec -S -p` exits with 1 if the crashed kernel is loaded and 0 if not.
    # Anything else (e.g. 255) is considered an error.
    rc = host.ssh_with_result("kexec -S -p").returncode
    assert rc in (0, 1), f"kexec -S -p failed with exit code {rc}"
    return rc == 1


def _xen_version(host: Host) -> str:
    """Return the running Xen version."""
    return host.ssh(
        "echo $(cat /sys/hypervisor/version/major).$(cat /sys/hypervisor/version/minor)"
        "$(cat /sys/hypervisor/version/extra)"
    )


def _crash_dirs(host: Host, crash_log_dir: str) -> set[str]:
    """Return the crash log directories, as named by /usr/sbin/kdump (YYYYmmdd-HHMMSS-TZ)."""
    out = host.ssh(f"ls {crash_log_dir}")
    return {d for d in out.splitlines() if re.fullmatch(r"\d{8}-\d{6}-\S+", d)}


@pytest.mark.usefixtures("host_at_least_8_3")
class TestKdumpSetup:
    def test_packages_installed(self, host: Host) -> None:
        host.ssh("rpm -q kexec-tools xen-crashdump-analyser")
        for path in ["/usr/sbin/kexec", "/usr/sbin/kdump", "/usr/sbin/xs-kdump",
                     _kdump_conf(host, "XCA")]:
            host.ssh(f"test -x {path}")

    def test_kdump_service_succeeded(self, host: Host) -> None:
        # Oneshot unit with RemainAfterExit: "active" plus Result=success
        # means xs-kdump exited 0 at boot.
        assert host.ssh("systemctl is-enabled kdump.service") == "enabled"
        state = host.ssh("systemctl show kdump.service -p ActiveState -p Result")
        assert set(state.splitlines()) == {"ActiveState=active", "Result=success"}, state

    def test_crashed_memory_reserved(self, host: Host) -> None:
        # Asks Xen for the crashkernel= region; no reservation means there is
        # nowhere to load the crashed kernel.
        size = int(host.ssh("kexec --print-ckr-size"))
        assert size > 0, "Xen has no crashed kernel memory reserved (crashkernel=)"
        logging.info("crashed kernel region: %d MiB", size // (1024 * 1024))

    def test_crashed_kernel_loaded(self, host: Host) -> None:
        assert _crashed_kernel_loaded(host), "no crashed kernel loaded into Xen"

    def test_kdump_kernel_and_initrd_present(self, host: Host) -> None:
        version = _kdump_conf(host, "KDUMP_KERNEL_VERSION")
        host.ssh(f"test -f /boot/vmlinuz-{version}")
        host.ssh(f"test -f /boot/initrd-{version}.img")

    def test_symbol_files_staged(self, host: Host) -> None:
        # The crashed kernel finds the symbol tables of the crashed Xen and
        # dom0 in /var/lib/kdump, copied there at boot by xs-kdump.
        xen = _xen_version(host)
        dom0 = host.ssh("uname -r")
        host.ssh(f"cmp /boot/xen-{xen}.map /var/lib/kdump/xen-{xen}.map")
        host.ssh(f"cmp /boot/System.map-{dom0} /var/lib/kdump/System.map-{dom0}")

    def test_sacrificial_space(self, host: Host) -> None:
        # xs-kdump reserves disk space that the crashed kernel deletes before
        # writing its logs, so a crash that filled the disk can still be
        # analysed. On failure it leaves a .failed marker instead.
        space = _kdump_conf(host, "CRASH_SPACE")
        size_mb = int(_kdump_conf(host, "CRASH_SPACE_SIZE_MB"))
        host.ssh(f"test ! -e {space}.failed")
        assert host.ssh(f"stat -c '%s' {space}") == f"{size_mb * 1024 * 1024}"

    def test_crashed_kernel_unload_and_reload(self, host: Host) -> None:
        try:
            host.ssh("kexec -p -u")
            assert not _crashed_kernel_loaded(host), "crashed kernel still loaded after unload"
            host.ssh("systemctl restart kdump.service")
            assert _crashed_kernel_loaded(host), "kdump.service did not reload the crashed kernel"
        finally:
            # Never leave the host without a crashed kernel.
            if not _crashed_kernel_loaded(host):
                host.ssh("systemctl restart kdump.service")

    def test_xca_version(self, host: Host) -> None:
        # --version prints the version but exits with EX_USAGE (64), so only
        # check the output.
        version = host.ssh("rpm -q --qf '%{VERSION}' xen-crashdump-analyser")
        out = host.ssh_with_result(f"{_kdump_conf(host, 'XCA')} --version").stdout
        assert f"version {version}" in out, out


@pytest.mark.reboot
@pytest.mark.host_crash
@pytest.mark.usefixtures("host_at_least_8_3")
class TestKdumpCrash:
    def test_hypervisor_crash_is_analysed(self, host: Host) -> None:
        # Never crash a physical machine
        assert host.is_nested, f"{host} is not a nested host, refusing to crash it"
        crash_log_dir = _kdump_conf(host, "CRASH_LOG_DIR")
        assert _crashed_kernel_loaded(host), "no crashed kernel loaded, the crash would just reboot the host"
        before = _crash_dirs(host, crash_log_dir)

        # Xen's 'C' debug key crashes the hypervisor on purpose. Delay it so
        # the ssh command can return first.
        logging.info("[%s] Trigger a Xen crash", host)
        host.ssh("systemd-run --on-active=2s xl debug-keys C")
        host.wait_for_host_down()
        # The crashed kernel has no network: the host only answers again after
        # the analysis is done and kdump has rebooted it.
        host.wait_for_host_up(timeout_secs=30 * 60)
        host.wait_for_ssh_reachable()
        host.wait_for_xapi_enabled()

        new = _crash_dirs(host, crash_log_dir) - before
        assert len(new) == 1, f"expected one new crash log directory in {crash_log_dir}, got {sorted(new)}"
        crash_dir = f"{crash_log_dir}/{new.pop()}"
        logging.info("[%s] Crash logs in %s: %s", host, crash_dir, " ".join(host.ssh(f"ls {crash_dir}").split()))

        # Written by /usr/sbin/kdump before and after running the analyser.
        for f in ["readelf-Wl.out", "readelf-Wn.out", "dmesg.kexec.log"]:
            host.ssh(f"test -s {crash_dir}/{f}")
        # Written by xen-crashdump-analyser.
        for f in ["xen-crashdump-analyser.log", "xen.log", "dom0.log"]:
            host.ssh(f"test -s {crash_dir}/{f}")
        host.ssh(f"test ! -e {crash_dir}/fs-full")
        # kdump runs the analyser under GNU time, whose report is the only
        # place its exit status is recorded.
        assert "Exit status: 0" in host.ssh(f"cat {crash_dir}/time-v.out"), \
            "xen-crashdump-analyser failed, see time-v.out and xen-crashdump-analyser.log"
        # The Xen console ring saved in xen.log shows this crash was ours.
        host.ssh(f"grep -q \"'C' pressed -> triggering crashdump\" {crash_dir}/xen.log")

        # After the reboot, the host is ready for the next crash again.
        assert _crashed_kernel_loaded(host), "crashed kernel not reloaded after the reboot"
        space = _kdump_conf(host, "CRASH_SPACE")
        host.ssh(f"test -f {space}")
