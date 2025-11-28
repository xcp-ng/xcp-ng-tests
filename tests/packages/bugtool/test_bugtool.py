import pytest

from lib.host import Host

# This smoke test runs xen-bugtool and verifies that the archive it generates
# contains some of the expected logs and files.
#
# Requirements:
# - an XCP-ng host

def verify_contains(host: Host, archive: str, files: list[str]):
    listing = host.ssh(['tar', '-jtf', archive])
    listing_clean = [x.split('/', maxsplit=1)[1] for x in listing.splitlines()]
    assert all(file in listing_clean for file in files)

class TestsBugtool:
    # Verify a minimal bugtool invocation that only queries certain capabilities
    def test_bugtool_entries(self, host):
        try:
            filename = host.ssh(['xen-bugtool', '-y', '-s',
                                 '--entries=xenserver-logs,xenserver-databases,system-logs'])
            verify_contains(host, filename,
                            [
                                "var/log/xensource.log",
                                "var/log/SMlog",
                                "xapi-db.xml",
                            ])
        finally:
            if filename:
                host.ssh(['rm', '-f', filename])

    # Verify that a full xen-bugtool invocation contains the most essential files
    def test_bugtool_all(self, host):
        try:
            filename = host.ssh(['xen-bugtool', '-y', '-s'])
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
        finally:
            if filename:
                host.ssh(['rm', '-f', filename])
