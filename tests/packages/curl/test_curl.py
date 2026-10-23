import pytest

from lib.host import Host

# Functional tests for the curl package (and its dependencies).
#
# Requirements:
# - an XCP-ng host (--hosts)

# Maps each URL to its expected curl exit code (0 = success).
CURL_TEST_URLS: dict[str, int] = {
    "ftp://ftp.slackware.com/welcome.msg": 0,
    "http://badssl.com/": 0,
    "http://example.tld": 6,

    # Negative Test of ca-certificates (on compromised certs)
    "https://dv-g3b-aaa-emudhra.we-have-a-problem.com/": 60,
    "https://dv-g3b-emudhra.we-have-a-problem.com/": 60,

    "https://httpbin.org/status/200": 0,
    "https://xcp-ng.org/": 0,
    "scp://demo:password@test.rebex.net/readme.txt": 0,
    "sftp://demo:password@test.rebex.net/readme.txt": 0,
}


@pytest.mark.parametrize("url,expected_returncode", CURL_TEST_URLS.items())
def test_curl_url(host: Host, url: str, expected_returncode: int) -> None:
    """curl exit code must match the expected value for the given URL."""
    result = host.ssh_with_result(
        f"curl --silent --show-error --fail --output /dev/null {url}"
    )
    assert result.returncode == expected_returncode, (
        f"curl returned {result.returncode} for {url} (expected {expected_returncode}).\n"
        f"stderr: {result.ssherr}"
    )
