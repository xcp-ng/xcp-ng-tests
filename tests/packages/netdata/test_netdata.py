import pytest
import subprocess

from lib.netutil import wrap_ip

# This test installs netdata and netdata-ui packages, verifies the service status,
# checks that the configuration is only accessible from the host, and verifies that
# the web UI is accessible through port 19999.
#
# Requirements:
# - an XCP-ng host

@pytest.mark.usefixtures("host_with_netdata")
class TestsNetdata:
    def __get_headers(host, port, path=None):
        url = f"http://{wrap_ip(host.hostname_or_ip)}:{port}"
        if path is not None:
            url += f"/{path}"
        process = subprocess.Popen(
            ["curl", "-XGET", "-k", "-I", "-s", url],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, _ = process.communicate()
        return stdout.decode().splitlines()

    # Verify the ActiveState for the netdata service
    def test_netdata_service(self, host):
        host.ssh(['systemctl', 'is-active', 'netdata.service'])

    # Netdata configuration should be accessible only from the host
    def test_netdata_conf(self, host):
        lines = TestsNetdata.__get_headers(host, 19999, "netdata.conf")
        assert lines[0].strip() == "HTTP/1.1 403 Forbidden"

        stdout = host.ssh(['curl', "-XGET", "-k", "-I", '-s', 'localhost:19999/netdata.conf'])
        lines = stdout.splitlines()
        assert lines[0].strip() == "HTTP/1.1 200 OK"

    # Verify the web UI is accessible. i.e. port 19999 is opened
    def test_netdata_webui(self, host):
        lines = TestsNetdata.__get_headers(host, 19999)
        assert lines[0].strip() == "HTTP/1.1 200 OK"
