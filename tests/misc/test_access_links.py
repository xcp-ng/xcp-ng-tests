import pytest

import hashlib
import shlex
from pathlib import Path

from lib import commands
from lib.commands import SSHCommandFailed
from lib.host import Host

from typing import Generator

# This test is designed to verify the accessibility of the XOA deployment script,
# also testing the following packages: ca-certificates, curl, wget, libssh2
#
# Requirements:
# - an XCP-ng host with xcp-ng-release >= 8.3.0.29
# - for the sftp/scp tests: the host's root password set in data.py
#   (HOSTS or HOST_DEFAULT_PASSWORD)

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
@pytest.mark.usefixtures("xfail_on_xcpng_9_0")  # TBD: fix emudhra cert
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
@pytest.mark.usefixtures("xfail_on_xcpng_9_0")  # TBD: fix emudhra cert
def test_wget_url(host: Host, url: str, expected_returncode: int) -> None:
    """ Functional tests for wget package using sample URLs also try negative checks"""
    result = host.ssh_with_result(f"wget --quiet --output-document=/dev/null {url}")
    assert result.returncode == expected_returncode, (
        f"wget returned {result.returncode} for {url} (expected {expected_returncode}).\n"
        f"stderr: {result.ssherr}"
    )


# sftp/scp tests, exercising libssh2 through curl.
#
# curl connects to the host's own sshd (127.0.0.1:22) to upload and download
# a file, checking its integrity with sha256.
#
# The server's host key is verified by libssh2 against $HOME/.ssh/known_hosts:
# HOME points to a temporary directory holding a known_hosts file generated
# with ssh-keyscan, leaving root's ~/.ssh untouched.
# A negative test checks that an unknown host key is rejected.

SSH_SCHEMES = ["sftp", "scp"]
SSH_TEST_FILE = "curl-testfile"
# "SSL peer certificate or SSH remote key was not OK"
CURL_ERROR_PEER_FAILED_VERIFICATION = 60

@pytest.fixture(scope="module")
def ssh_workdir(host: Host) -> Generator[Path, None, None]:
    if not host.password:
        pytest.fail("no root password for this host in data.py")
    workdir = Path(host.ssh("mktemp -d"))
    try:
        host.ssh(f"mkdir -m 700 {workdir / '.ssh'}")
        host.ssh(f"ssh-keyscan 127.0.0.1 > {workdir / '.ssh/known_hosts'}")
        host.ssh(f"head -c 32M /dev/urandom > {workdir / SSH_TEST_FILE}")
        yield workdir
    finally:
        host.ssh(f"rm -rf {workdir}")

@pytest.fixture(scope="module")
def untrusted_home(host: Host, ssh_workdir: Path) -> Generator[Path, None, None]:
    # known_hosts only holds a freshly generated key, which can't match the server's one
    home = ssh_workdir / "untrusted-home"
    fake_key = home / "fake_host_key"
    try:
        host.ssh(f"mkdir -p -m 700 {home / '.ssh'}")
        host.ssh(f"ssh-keygen -q -t ed25519 -N '' -f {fake_key}")
        host.ssh(f"echo \"127.0.0.1 $(cat {fake_key}.pub)\" > {home / '.ssh/known_hosts'}")
        yield home
    finally:
        host.ssh(f"rm -rf {home}")

def _curl_ssh(host: Host, home: Path, args: str) -> None:
    credentials = shlex.quote(f"root:{host.password}")
    host.ssh(f"HOME={home} curl --silent --show-error --user {credentials} {args}")

def _ssh_url(scheme: str, path: Path) -> str:
    return f"{scheme}://127.0.0.1{path}"

@pytest.mark.parametrize("scheme", SSH_SCHEMES)
def test_curl_ssh_download(host: Host, ssh_workdir: Path, scheme: str) -> None:
    source = ssh_workdir / SSH_TEST_FILE
    downloaded = ssh_workdir / f"downloaded-{scheme}"
    _curl_ssh(host, ssh_workdir, f"--output {downloaded} {_ssh_url(scheme, source)}")
    assert host.sha256sum(downloaded) == host.sha256sum(source)

@pytest.mark.parametrize("scheme", SSH_SCHEMES)
def test_curl_ssh_upload(host: Host, ssh_workdir: Path, scheme: str) -> None:
    source = ssh_workdir / SSH_TEST_FILE
    uploaded = ssh_workdir / f"uploaded-{scheme}"
    _curl_ssh(host, ssh_workdir, f"--upload-file {source} {_ssh_url(scheme, uploaded)}")
    assert host.sha256sum(uploaded) == host.sha256sum(source)

@pytest.mark.parametrize("scheme", SSH_SCHEMES)
def test_curl_ssh_unknown_host_key_rejected(host: Host, ssh_workdir: Path, untrusted_home: Path,
                                            scheme: str) -> None:
    source = ssh_workdir / SSH_TEST_FILE
    rejected = ssh_workdir / f"rejected-{scheme}"
    with pytest.raises(SSHCommandFailed) as excinfo:
        _curl_ssh(host, untrusted_home, f"--output {rejected} {_ssh_url(scheme, source)}")
    assert excinfo.value.returncode == CURL_ERROR_PEER_FAILED_VERIFICATION
