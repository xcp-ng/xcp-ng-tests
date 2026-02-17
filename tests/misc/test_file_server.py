import pytest

import re

import lib.commands as commands
from lib.host import Host
from lib.netutil import wrap_ip

# These tests are meant to test an host fileserver behavior.
#
# Requirements:
# - an XCP-ng host >= 8.2 with latest updates (and >= 8.3 for the HSTS test).
#   The host must be configured with `website-https-only` set to true (which is the default config).

def _header_equal(header: str, name: str, value: str) -> bool:
    regex = fr"{name}:\s?{re.escape(value)}"
    return re.match(regex, header) is not None

def test_fileserver_redirect_https(host: Host) -> None:
    path = "/path/to/dir/file.txt"
    ip = wrap_ip(host.hostname_or_ip)
    res = commands.local_cmd(["curl", "-s", "-i", "http://" + ip + path])
    assert isinstance(res.stdout, str)
    lines = res.stdout.splitlines()
    assert lines[0].strip() == "HTTP/1.1 301 Moved Permanently"
    assert _header_equal(lines[2], "location", "https://" + ip + path)

@pytest.mark.usefixtures("host_at_least_8_3")
class TestHSTS:
    HSTS_HEADER_NAME = "strict-transport-security"
    HSTS_HEADER_VALUE = "max-age=63072000"

    @staticmethod
    def __get_header(host: Host) -> list[str]:
        res = commands.local_cmd(
            ["curl", "-s", "-XGET", "-k", "-I", "https://" + wrap_ip(host.hostname_or_ip)]
        )
        assert isinstance(res.stdout, str)
        return res.stdout.splitlines()

    def test_fileserver_hsts_default(self, host: Host) -> None:
        # By default HSTS header should not be set
        for line in TestHSTS.__get_header(host):
            assert not _header_equal(line, TestHSTS.HSTS_HEADER_NAME, TestHSTS.HSTS_HEADER_VALUE)

    def test_fileserver_hsts(self, host_with_hsts: Host) -> None:
        hsts_header_found = False

        for line in TestHSTS.__get_header(host_with_hsts):
            if _header_equal(line, TestHSTS.HSTS_HEADER_NAME, TestHSTS.HSTS_HEADER_VALUE):
                hsts_header_found = True
                break

        assert hsts_header_found
