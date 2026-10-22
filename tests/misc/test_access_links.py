import pytest

import hashlib

from lib import commands
from lib.host import Host

# This test is designed to verify the accessibility of the XOA deployment script,
# also testing the following packages: ca-certificates, curl, wget
#
# Requirements:
# - an XCP-ng host with xcp-ng-release >= 8.3.0.29

@pytest.mark.parametrize("command_id", ["curl", "wget"])
@pytest.mark.parametrize("url_id", [
    "xoa",
    "xcpng",
    "vates"
])
def test_access_links(host: Host, command_id: str, url_id: str) -> None:
    """
    Verifies that the specified URL responds correctly via the specified command
    and compares the checksum of the downloaded content between local and remote.
    """
    command = {"curl": ["curl", "-fsSL"],
               "wget": ["wget", "-qO-"]}[command_id]
    url = {
        "xoa": "https://xoa.io/deploy",
        "xcpng": "https://updates.xcp-ng.org/README.txt",
        "vates": "https://repo.vates.tech/README.txt"
    }[url_id]
    COMMAND = command + [url]
    COMMAND_STR = " ".join(COMMAND)

    # Download from remote host
    remote_result = host.ssh(COMMAND_STR)

    # Verify the download worked by comparing with local download
    # This ensures the content is accessible and identical from both locations
    local_result = commands.local_cmd(COMMAND)

    assert local_result.returncode == 0, f"Failed to fetch URL locally: {local_result.stdout}"

    # Extract checksums
    local_checksum = hashlib.sha256(local_result.stdout.split()[0].encode('utf-8')).hexdigest()
    remote_checksum = hashlib.sha256(remote_result.split()[0].encode('utf-8')).hexdigest()

    assert local_checksum == remote_checksum, (
        f"Checksum mismatch: local ({local_checksum}) != remote ({remote_checksum})"
    )


# Maps each URL to a dict of expected exit codes per command.
TEST_URLS: dict[str, dict[str, int]] = {
    "ftp://ftp.slackware.com/welcome.msg": {"curl": 0, "wget": 0},  # in wget-1 not 2
    "http://badssl.com/": {"curl": 0, "wget": 0}, # HTTP/1.1 200 OK
    "http://example.tld": {"curl": 6, "wget": 4},  # unresolvable
    "https://dv-g3b-aaa-emudhra.we-have-a-problem.com/": {"curl": 60, "wget": 5},  # test ca-certificates
    "https://dv-g3b-emudhra.we-have-a-problem.com/": {"curl": 60, "wget": 5},  # SSL negative test
    "https://httpbin.org/status/500": {"curl": 22, "wget": 8},  # Server error
    "https://updates.xcp-ng.org/default.asp": {"curl": 22, "wget": 8},  # Client error (404)
    "https://koji.xcp-ng.org/robots.txt": {"curl": 0, "wget": 0},  # HTTP/1.1 200 OK
}


@pytest.mark.parametrize(
    "url,expected_returncode",
    [(url, codes["curl"]) for url, codes in TEST_URLS.items() if "curl" in codes],
)
def test_curl_url(host: Host, url: str, expected_returncode: int) -> None:
    """ Functional tests for curl package using sample URLs also try negative checks"""
    result = host.ssh_with_result(
        f"curl --silent --show-error --fail --output /dev/null {url}"
    )
    assert result.returncode == expected_returncode, (
        f"curl returned {result.returncode} for {url} (expected {expected_returncode}).\n"
        f"stderr: {result.ssherr}"
    )


@pytest.mark.parametrize(
    "url,expected_returncode",
    [(url, codes["wget"]) for url, codes in TEST_URLS.items() if "wget" in codes],
)
def test_wget_url(host: Host, url: str, expected_returncode: int) -> None:
    """ Functional tests for wget package using sample URLs also try negative checks"""
    result = host.ssh_with_result(f"wget --quiet --output-document=/dev/null {url}")
    assert result.returncode == expected_returncode, (
        f"wget returned {result.returncode} for {url} (expected {expected_returncode}).\n"
        f"stderr: {result.ssherr}"
    )
