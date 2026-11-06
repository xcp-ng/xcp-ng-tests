import pytest

import shlex
from pathlib import Path

from lib.host import Host

from typing import Generator

# Functional tests for the libssh2 package, exercised through curl, which
# relies on libssh2 for its scp:// and sftp:// protocols.
#
# curl runs on the host and connects to the host's own sshd (127.0.0.1:22),
# to upload and download a file, checking its integrity with sha256.
#
# Authentication uses the host's root password from data.py.
# Host key verification is not the purpose of these tests, hence --insecure.
#
# Requirements:
# - an XCP-ng host (--hosts) >= 8.3, with curl built against libssh2
# - the host's root password set in data.py (HOSTS or HOST_DEFAULT_PASSWORD)

SCHEMES = ["sftp", "scp"]
TEST_FILE = "curl-testfile"

@pytest.fixture(scope="module")
def workdir(host: Host) -> Generator[Path, None, None]:
    if not host.password:
        pytest.skip("no root password for this host in data.py")
    workdir = Path(host.ssh("mktemp -d"))
    try:
        host.ssh(f"head -c 32M /dev/urandom > {workdir}/{TEST_FILE}")
        yield workdir
    finally:
        host.ssh(f"rm -rf {workdir}")

def _curl(host: Host, args: str) -> None:
    credentials = shlex.quote(f"root:{host.password}")
    host.ssh(f"curl --silent --show-error --insecure -u {credentials} {args}")

def _url(scheme: str, path: Path) -> str:
    return f"{scheme}://127.0.0.1{path}"

def _sha256(host: Host, path: Path) -> str:
    return host.ssh(f"sha256sum {path}").split()[0]

@pytest.mark.parametrize("scheme", SCHEMES)
def test_curl_download(host: Host, workdir: Path, scheme: str) -> None:
    downloaded = workdir / f"downloaded-{scheme}"
    _curl(host, f"-o {downloaded} {_url(scheme, workdir / TEST_FILE)}")
    assert _sha256(host, downloaded) == _sha256(host, workdir / TEST_FILE)

@pytest.mark.parametrize("scheme", SCHEMES)
def test_curl_upload(host: Host, workdir: Path, scheme: str) -> None:
    uploaded = workdir / f"uploaded-{scheme}"
    _curl(host, f"-T {workdir / TEST_FILE} {_url(scheme, uploaded)}")
    assert _sha256(host, uploaded) == _sha256(host, workdir / TEST_FILE)
