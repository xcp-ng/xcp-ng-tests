import logging
import json
import pytest

# Requirements:
# From --hosts parameter:
# - host: XCP-ng host >= 8.2

@pytest.mark.usefixtures("host_with_dom0_tests")
class TestXenDom0Tests:
    def test_dom0(self, host):
        metadata = host.ssh(['cat', '/usr/share/xen-dom0-tests-metadata.json'])
        tests = json.loads(metadata)["tests"]
        assert tests, "Test list must not be empty"
        for test in tests:
            logging.info(f"Running {test}...")
            host.ssh([f'/usr/libexec/xen/bin/{test}'])
