import pytest

import subprocess

from lib.common import Defer
from lib.host import Host

# This smoke test runs xen-bugtool and verifies that the archive it generates
# contains some of the expected logs and files.
#
# Requirements:
# - an XCP-ng host

def verify_contains(host: Host, archive: str, files: list[str]) -> None:
    listing = host.ssh(f'tar -jtf {archive}')
    listing_clean = [x.split('/', maxsplit=1)[1] for x in listing.splitlines()]
    assert all(file in listing_clean for file in files)

class TestsBugtool:
    # Verify a minimal bugtool invocation that only queries certain capabilities
    def test_bugtool_entries(self, host: Host, defer: Defer) -> None:
        filename = host.ssh('xen-bugtool -y -s --entries=xenserver-logs,xenserver-databases,system-logs')
        defer(lambda: host.ssh(f'rm -f {filename}'))
        verify_contains(host, filename,
                        [
                            "var/log/xensource.log",
                            "var/log/SMlog",
                            "xapi-db.xml",
                        ])

    # Verify that a full xen-bugtool invocation contains the most essential files
    def test_bugtool_all(self, host: Host, defer: Defer) -> None:
        filename = host.ssh('xen-bugtool -y -s')
        defer(lambda: host.ssh(f'rm -f {filename}'))
        verify_contains(host, filename,
                        [
                            "var/log/xensource.log",
                            "var/log/SMlog",
                            "xapi-db.xml",
                            "acpidump.out",
                            "etc/fstab",
                            "etc/xapi.conf",
                            "etc/xensource/pool.conf",
                        ])
