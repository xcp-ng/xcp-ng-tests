import pytest
import ssl
import socket
import logging

# This test is designed to verify that TLS connections is secured
#
# Requirements:
# - An XCP-ng host

@pytest.mark.parametrize("protocol_name", ["TLSv1", "TLSv1.1"])
def test_tls_disabled(host: str, protocol_name: str):
    """
    Verifies that specified TLS protocols are disabled on the XCP-ng host.
    Uses the ssl library directly. Should raise SSLError.
    """
    PORT = 443

    protocol = {
        "TLSv1": ssl.PROTOCOL_TLSv1,
        "TLSv1.1": ssl.PROTOCOL_TLSv1_1
    }[protocol_name]

    logging.info(f"Testing if protocol {protocol_name} is disabled on host {host}")

    with pytest.raises(ssl.SSLError):
        context = ssl.SSLContext(protocol)
        with socket.create_connection((str(host), PORT), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=str(host)) as ssock:
                ssock.do_handshake()
                # If we reach this point, the protocol is enabled (test should fail)
                pytest.fail(f"Protocol {protocol} should be disabled but connection succeeded")

@pytest.mark.parametrize("protocol_name", ["TLSv1.2"])
def test_enabled(host: str, protocol_name: str):
    """
    Verifies that TLSv1.2 is enabled on the XCP-ng host.
    Uses the ssl library directly.
    """
    PORT = 443

    protocol = {
        "TLSv1.2": ssl.PROTOCOL_TLSv1_2
    }[protocol_name]

    logging.info(f"Testing if protocol {protocol_name} is enabled on host {host}")

    try:
        context = ssl.SSLContext(protocol)
        with socket.create_connection((str(host), PORT), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=str(host)) as ssock:
                ssock.do_handshake()
                assert ssock.version()
    except ssl.SSLError as e:
        pytest.fail(f"{protocol_name} should be enabled, but got SSLError: {e}")
