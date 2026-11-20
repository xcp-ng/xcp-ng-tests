import pytest

import logging
import pathlib

from packaging import version

from lib.host import Host

from typing import Generator

# Requirements:
# From --hosts parameter:
# - host: XCP-ng host >= 9.0, booted with Secure Boot enabled
#
# The test_hypercall_filter helper binary is built from:
# TODO: <link to the source repository of test_hypercall_filter>

LOCAL_BINARY = pathlib.Path(__file__).parent / "data" / "test_hypercall_filter"


@pytest.fixture(scope="module")
def host_with_secure_boot(host: Host) -> Host:
    if host.xcp_version < version.parse("9.0"):
        pytest.skip("This test requires an XCP-ng >= 9.0 host")
    logging.info("Checking that the host is booted with Secure Boot enabled")
    sb_state = host.ssh("mokutil --sb-state")
    if "SecureBoot enabled" not in sb_state:
        pytest.skip(f"This test requires a host booted with Secure Boot enabled (got: {sb_state!r})")
    return host


@pytest.fixture(scope="module")
def hypercall_filter_binary(host_with_secure_boot: Host) -> Generator[str, None, None]:
    host = host_with_secure_boot
    tmp_dir = host.ssh("mktemp -d")
    remote_binary = f"{tmp_dir}/test_hypercall_filter"
    try:
        host.scp(str(LOCAL_BINARY), remote_binary)
        host.ssh(f"chmod +x {remote_binary}")
        yield remote_binary
    finally:
        host.ssh(f"rm -rf {tmp_dir}")


def test_hypercall_filter(host: Host, hypercall_filter_binary: str) -> None:
    """
    Verify the Xen privcmd hypercall filter.

    The helper binary performs a collection of safe hypercalls and verifies that:
      * allowed read-only hypercalls succeed
      * invalid guest pointers are rejected with -EFAULT
      * forbidden operations are rejected by the filter
      * unknown operations return the expected error

    The helper exits with status 0 iff every check passes.
    """
    host.ssh(hypercall_filter_binary)
