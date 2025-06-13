import logging

import pytest

from lib.commands import SSHCommandFailed

# Requirements:
# From --hosts parameter:
# - host: XCP-ng host >= 8.2, with Xen booted with the hvm_fep command line parameter
#         See https://xenbits.xen.org/docs/xtf/

@pytest.mark.usefixtures("host_with_hvm_fep", "host_with_dynamically_disabled_ept_sp")
class TestXtf:
    _common_skips = [
        # UMIP requires hardware support, that is a recent enough CPU
        'test-hvm32-umip',
        'test-hvm64-umip',
        # PV Superpages, a thing which was removed long ago from the hypervisor. Always skips
        'test-pv64-xsa-167',
        # Depends on pv linear pagetables, which is disabled by default but can be activated on Xen's cmdline.
        # Is not needed for Linux. It is for a NetBSD PV guest.
        'test-pv64-xsa-182',
        # Will skip if DBEXT support is not present
        'test-pv64-xsa-444',
    ]

    def _extract_skipped_tests(self, output):
        skipped_tests = []
        for line in output.splitlines():
            if line.endswith(' SKIP'):
                skipped_tests.append(line.split()[0])
        return skipped_tests

    def test_self(self, host, xtf_runner):
        logging.info("Running selftest...")
        host.ssh([xtf_runner, 'selftest', '-q', '--host'])

    def test_all(self, host, xtf_runner):
        logging.info("Running tests...")
        try:
            host.ssh([xtf_runner, '-aqq', '--host'])
        except SSHCommandFailed as e:
            if e.returncode == 3: # 3 means there were skipped tests
                # Check that the skipped tests belong to the list of common expected skips
                skipped_tests = self._extract_skipped_tests(e.stdout)
                if not skipped_tests:
                    raise
                logging.info(f"Tests {' '.join(skipped_tests)} were skipped. "
                             "Checking whether they belong to the allowed list...")
                for skipped_test in skipped_tests:
                    if skipped_test not in self._common_skips:
                        logging.error("... At least one doesn't")
                        raise
                logging.info("... They do")
            else:
                raise
