import subprocess

# These tests are meant to test an host fileserver behavior.
#
# Requirements:
# - an XCP-ng host >= 8.2 with latest updates.
#   The host must be configured with `website-https-only` set to true (which is the default config).

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
    assert lines[2].strip() == "location:https://" + host.hostname_or_ip + path

class TestHSTS:
    HSTS_HEADER = "strict-transport-security:max-age=63072000"

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
            assert line != TestHSTS.HSTS_HEADER

    def test_fileserver_hsts(self, host_with_hsts):
        hsts_header_found = False

        for line in TestHSTS.__get_header(host_with_hsts):
            if line == TestHSTS.HSTS_HEADER:
                hsts_header_found = True
                break

        assert hsts_header_found
