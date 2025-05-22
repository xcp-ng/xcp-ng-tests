import pytest
from lib.commands import SSHCommandFailed

# This test is designed to verify that TLS connections is secured
#
# Requirements:
# - XCP-ng 

def test_tlsv1_disabled(host):
    """
    Verifies that TLSv1 is disabled on the XCP-ng host.
    Uses the openssl command via SSH.
    """
    try:
        result = host.ssh("openssl s_client -connect localhost:443 -tls1")
        pytest.fail(f"TLSv1 should be disabled, but got: {result}")
    except SSHCommandFailed as e:
        result = e
        assert (
            "handshake failure" in str(result)
        ), f"TLSv1 should be disabled, but got: {result}"

def test_tlsv1_1_disabled(host):
    """
    Verifies that TLSv1.1 is disabled on the XCP-ng host.
    Uses the openssl command via SSH.
    """
    try:
        result = host.ssh("openssl s_client -connect localhost:443 -tls1_1")
        pytest.fail(f"TLSv1.1 should be disabled, but got: {result}")
    except SSHCommandFailed as e:
        result = e
        assert (
            "handshake failure" in str(result)
        ), f"TLSv1.1 should be disabled, but got: {result}"
    
def test_tlsv2_enabled(host):
    """
    Verifies that TLSv2 is enabled on the XCP-ng host.
    Uses the openssl command via SSH.
    """
    result = host.ssh("openssl s_client -connect localhost:443 -tls1_2")
    assert (
        "handshake failure" not in str(result)
    ), f"TLSv2 should be enabled, but got: {result}"