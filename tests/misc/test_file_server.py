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
