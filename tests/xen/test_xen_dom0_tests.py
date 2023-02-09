import logging
import pytest

# Requirements:
# From --hosts parameter:
# - host: XCP-ng host >= 8.2

@pytest.mark.usefixtures("host_with_dom0_tests")
class TestXenDom0Tests:
    def test_cpu_policy(self, host):
        logging.info("Running test-cpu-policy...")
        host.ssh(['/usr/libexec/xen/bin/test-cpu-policy'])

    def test_xenstore(self, host):
        logging.info("Running test-xenstore...")
        host.ssh(['/usr/libexec/xen/bin/test-xenstore'])
