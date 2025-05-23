import pytest
from lib.commands import SSHCommandFailed

# This test is designed to verify that TLS connections is secured
#
# Requirements:
# - An XCP-ng host

@pytest.mark.parametrize("protocol", ["tls1", "tls1_1"])
def test_tls_disabled(host, protocol):
    """
    Verifies that specified TLS protocols are disabled on the XCP-ng host.
    Uses the openssl command via SSH. (Should raise an error)
    """
    try:
        host.ssh(f"openssl s_client -connect localhost:443 -{protocol}")
    except SSHCommandFailed as e:
        assert "handshake failure" in e.stdout, (
            f"{protocol.upper()} should return 'handshake failure' "
            f"but got: {e.stdout}"
        )
        return
    assert False, f"{protocol.upper()} should be disabled, but no error was raised."

@pytest.mark.parametrize("protocol", ["tls1_2"])
def test_enabled(host, protocol):
    """
    Verifies that TLSv2 is enabled on the XCP-ng host.
    Uses the openssl command via SSH.
    """
    result = host.ssh(f"openssl s_client -connect localhost:443 -{protocol}")
    assert (
        "handshake failure" not in str(result)
    ), f"TLSv2 should be enabled, but got: {result}"
