import pytest
import re
import subprocess

# These tests are meant to test an host fileserver behavior.
#
# Requirements:
# - an XCP-ng host >= 8.2 with latest updates (and >= 8.3 for the HSTS test).
#   The host must be configured with `website-https-only` set to true (which is the default config).

def _header_equal(header, name, value):
    regex = fr"{name}:\s?{value}"
    return re.match(regex, header) is not None

def test_fileserver_redirect_https(host):
    path = "/path/to/dir/file.txt"
    process = subprocess.Popen(
        ["curl", "-i", "http://" + host.hostname_or_ip + path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    stdout, _ = process.communicate()
    lines = stdout.decode().splitlines()
    assert lines[0].strip() == "HTTP/1.1 301 Moved Permanently"
    assert _header_equal(lines[2], "location", "https://" + host.hostname_or_ip + path)

@pytest.mark.usefixtures("host_at_least_8_3")
class TestHSTS:
    HSTS_HEADER_NAME = "strict-transport-security"
    HSTS_HEADER_VALUE = "max-age=63072000"

    def __get_header(host):
        process = subprocess.Popen(
            ["curl", "-XGET", "-k", "-I", "https://" + host.hostname_or_ip],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, _ = process.communicate()
        return stdout.decode().splitlines()

    def test_fileserver_hsts_default(self, host):
        # By default HSTS header should not be set
        for line in TestHSTS.__get_header(host):
            assert not _header_equal(line, TestHSTS.HSTS_HEADER_NAME, TestHSTS.HSTS_HEADER_VALUE)

    def test_fileserver_hsts(self, host_with_hsts):
        hsts_header_found = False

        for line in TestHSTS.__get_header(host_with_hsts):
            if _header_equal(line, TestHSTS.HSTS_HEADER_NAME, TestHSTS.HSTS_HEADER_VALUE):
                hsts_header_found = True
                break

        assert hsts_header_found
