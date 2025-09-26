import pytest

import subprocess

# This smoke test runs xen-bugtool and verifies that the archive it generates
# contains some of the expected logs and files.
#
# Requirements:
# - an XCP-ng host

class TestsBugtool:
    def verify_contains(host, archive, files):
        listing = host.ssh(['tar', '-jtf', archive])
        listing = [x.split('/', maxsplit=1)[1] for x in listing.splitlines()]
        assert all(file in listing for file in files)

    # Verify a minimal bugtool invocation that only queries certain capabilities
    def test_bugtool_entries(self, host):
        try:
            filename = host.ssh(['xen-bugtool', '-y', '-s',
                                 '--entries=xenserver-logs,xenserver-databases,system-logs'])
            TestsBugtool.verify_contains(host, filename,
                                         [
                                             "var/log/xensource.log",
                                             "var/log/SMlog",
                                             "xapi-db.xml",
                                         ])
        finally:
            host.ssh(['rm', '-f', '/var/opt/xen/bug-report/*'])

    # Verify that a full xen-bugtool invocation contains the most essential files
    def test_bugtool_all(self, host):
        try:
            filename = host.ssh(['xen-bugtool', '-y', '-s'])
            TestsBugtool.verify_contains(host, filename,
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
            host.ssh(['rm', '-f', '/var/opt/xen/bug-report/*'])
