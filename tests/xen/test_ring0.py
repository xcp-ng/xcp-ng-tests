import logging
import secrets
import time
from typing import Generator, Optional

import pytest

from lib.host import Host
from lib.vm import VM

# Requirements:
# From --hosts parameter:
# - host: XCP-ng host >= 8.2, reboots after test
# From --vm parameter:
# - A VM to import
# Only XST tests with pass/fail results, and that don't crash the host were included.


@pytest.fixture(scope="package")
def host_with_ring0_tests(
    host_with_saved_yum_state: Host,
) -> Generator[Host, None, None]:
    host = host_with_saved_yum_state
    host.yum_install(["test-ring0"])
    yield host
    # clean up the loaded test modules and test states at the end
    host.reboot(verify=True)


@pytest.fixture
def host_without_livepatch_loaded(host: Host) -> Host:
    if host.ssh_with_result("lsmod | grep -wq livepatch_tester").returncode == 0:
        pytest.fail("livepatch_tester already loaded, host needs reboot")
    return host


def do_execute_xst(host: Host, modname: str, testname: Optional[str] = None) -> None:
    if testname is None:
        testname = modname
    host.ssh(f"modprobe xst_{modname}")
    try:
        host.ssh(f"echo 1 > /sys/kernel/debug/xst/{testname}/run")
        host.ssh(f"grep -q 'status: pass' /sys/kernel/debug/xst/{testname}/results")
    finally:
        host.ssh(f"modprobe -r xst_{modname}", check=False)


@pytest.mark.reboot  # host_with_ring0_tests
@pytest.mark.usefixtures("host_with_ring0_tests")
class TestRing0Tests:
    def test_privcmd_restrict(self, host: Host):
        host.ssh("/usr/bin/privcmd-restrict_test")

    def test_xst_alloc_balloon(self, host: Host):
        do_execute_xst(host, "alloc_balloon")

    def test_xst_big_module(self, host: Host):
        do_execute_xst(host, "big_module")

    def test_xst_evtchn_latency(self, host: Host):
        do_execute_xst(host, "evtchn_latency", "evtchn_lat")

    @pytest.mark.skip("only makes sense for 2l evtchn")
    def test_xst_evtchn_limit(self, host: Host):
        do_execute_xst(host, "evtchn_limit")

    def test_xst_evtchn_stress(self, host: Host):
        do_execute_xst(host, "evtchn_stress")

    @pytest.mark.skip("leaks event channels infinitely")
    def test_xst_evtchn_unbind(self, host: Host):
        do_execute_xst(host, "evtchn_unbind")

    def test_xst_get_user_pages(self, host: Host):
        host.ssh("modprobe xst_get_user_pages")
        try:
            host.ssh("/usr/bin/gup_test")
        finally:
            host.ssh("modprobe -r xst_get_user_pages", check=False)

    def test_xst_grant_copy_perf(self, host: Host):
        do_execute_xst(host, "grant_copy_perf", "gntcpy_perf")

    @pytest.mark.small_vm
    def test_xst_ioemu_msi(self, host: Host, running_unix_vm: VM):
        # TODO: validate MSI reception in guest
        vm = running_unix_vm
        domid = vm.param_get("dom-id")
        host.ssh("modprobe xst_ioemu_msi")
        try:
            host.ssh(f"echo {domid} > /sys/kernel/debug/xst/ioemu_msi/domid")
            host.ssh("echo 1 > /sys/kernel/debug/xst/ioemu_msi/data")
            host.ssh("echo 1 > /sys/kernel/debug/xst/ioemu_msi/run")
            host.ssh("grep -q 'status: pass' /sys/kernel/debug/xst/ioemu_msi/results")
        finally:
            host.ssh("modprobe -r xst_ioemu_msi", check=False)

    @pytest.mark.usefixtures("host_at_least_8_3")
    def test_xst_livepatch(self, host_without_livepatch_loaded: Host):
        """
        This test loads a `livepatch_testee` module, and triggers the test
        function `test_function_default` by writing to
        `/proc/livepatch_testee/cmd`. This function is then updated by loading
        `livepatch_tester` and retested. Distinguish between unpatched and
        patched functions using printk output, delimited by a random marker.

        Strangely enough, the patch in `livepatch_tester` causes the patched
        `test_function_crash` to crash the host (instead of the other way
        around). So don't test that.
        """
        host = host_without_livepatch_loaded
        try:
            host.ssh("modprobe livepatch_testee")

            marker = secrets.token_hex()
            logging.debug(f"using pre-patch marker {marker}")
            host.ssh(f"echo {marker} > /dev/kmsg")
            host.ssh("echo 1 > /proc/livepatch_testee/cmd")
            host.ssh(f"dmesg | grep -A 9999 {marker} | grep -q test_function_default_old")

            host.ssh("modprobe livepatch_tester")

            marker = secrets.token_hex()
            logging.debug(f"using post-patch marker {marker}")
            host.ssh(f"echo {marker} > /dev/kmsg")
            host.ssh("echo 1 > /proc/livepatch_testee/cmd")
            host.ssh(f"dmesg | grep -A 9999 {marker} | grep -q test_function_default_new")
        finally:
            host.ssh("modprobe -r livepatch_testee", check=False)

    def test_xst_memory_leak(self, host: Host):
        if not host.file_exists("/sys/kernel/debug/kmemleak"):
            pytest.skip("CONFIG_DEBUG_KMEMLEAK is not set")

        host.ssh("modprobe xst_memory_leak")

        try:
            host.ssh("echo clear > /sys/kernel/debug/kmemleak")
            host.ssh("echo 1 > /sys/kernel/debug/xst/memleak/run")
            host.ssh("modprobe -r xst_memory_leak")
            host.ssh("echo scan > /sys/kernel/debug/kmemleak")
            # scan twice with a delay inbetween, otherwise the leak may not show up
            time.sleep(5)
            host.ssh("echo scan > /sys/kernel/debug/kmemleak")
            host.ssh("grep -q unreferenced /sys/kernel/debug/kmemleak")
        finally:
            host.ssh("modprobe -r xst_memory_leak", check=False)

    def test_xst_pte_set_clear_flags(self, host: Host):
        do_execute_xst(host, "pte_set_clear_flags")

    def test_xst_ptwr_xchg(self, host: Host):
        do_execute_xst(host, "ptwr_xchg")

    def test_xst_set_memory_uc(self, host: Host):
        do_execute_xst(host, "set_memory_uc")

    @pytest.mark.skip("crashes the host, disabled by default")
    def test_xst_soft_lockup(self, host: Host):
        do_execute_xst(host, "soft_lockup")
