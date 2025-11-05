import pytest

import logging

from lib.host import Host
from lib.vm import VM

# This test file demonstrates the automatic log collection mechanism
# When tests fail, logs will be automatically collected from the hosts involved
# Console screenshots are captured only for tests marked with @pytest.mark.capture_console


class TestLogCollection:
    """Test class to verify automatic log collection on failure (no console capture)."""

    def test_passing_test(self, host: Host):
        """This test passes, so no logs should be collected for it."""
        logging.info(f"Testing on host: {host.hostname_or_ip}")
        result = host.ssh("echo 'Hello from passing test'")
        assert "Hello" in result

    def test_failing_test(self, host: Host):
        """This test fails intentionally to trigger log collection (but no console)."""
        logging.info(f"Testing on host: {host.hostname_or_ip}")
        # This assertion will fail, triggering log collection (but no console capture)
        assert False, "Intentional failure to test log collection mechanism"

    @pytest.mark.skip(reason="Example of skipped test - no logs collected")
    def test_skipped_test(self, host: Host):
        """Skipped tests don't trigger log collection."""
        assert False

    # Its sole purpose is to test collection on multiple failures.
    # Log collection is supposed to happen at the end of session.
    def test_another_failure(self, host: Host):
        """Another failing test."""
        logging.info("This test will also fail")
        host.ssh("cat /nonexistent/file", check=False)
        # Force a failure
        assert False, "Second intentional failure"

    def test_yet_another_pass(self, host: Host):
        """Another passing test."""
        assert host.ssh("hostname")

@pytest.mark.capture_console
class TestWithConsoleCapture:
    """Tests in this class will capture VM console screenshots on failure."""

    def test_vm_failure_with_console(self, running_vm: VM):
        """This test fails with a running VM - console will be captured."""
        vm = running_vm
        logging.info(f"Testing VM {vm.uuid} on host {vm.host.hostname_or_ip}")
        logging.info(f"VM power state: {vm.power_state()}")
        # This will fail and trigger both log collection AND console capture
        assert False, "Intentional failure to test console capture"

    def test_vm_passing_no_console(self, running_vm: VM):
        """This test passes - no console capture needed."""
        vm = running_vm
        logging.info(f"VM {vm.uuid} is running")
        assert vm.is_running()
