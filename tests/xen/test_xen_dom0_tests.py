import pytest

import json
import logging

from lib.host import Host

# Requirements:
# From --hosts parameter:
# - host: XCP-ng host >= 8.2

@pytest.mark.usefixtures("host_with_dom0_tests")
class TestXenDom0Tests:
    def test_dom0(self, host: Host) -> None:
        metadata = json.loads(host.ssh('cat /usr/share/xen-dom0-tests-metadata.json'))
        tests = metadata["tests"]
        assert tests, "Test list must not be empty"
        testdir = metadata.get("testdir", "/usr/libexec/xen/bin")
        for test in tests:
            logging.info(f"Running {test}...")
            host.ssh(f'{testdir}/{test}')
