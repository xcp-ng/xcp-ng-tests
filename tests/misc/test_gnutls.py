import pytest

import logging
import socket
import ssl
from pathlib import Path

from lib.host import Host

from typing import Generator, TypedDict

# Test gnutls with gnutls-utils:
# Validate certificates generation and usage with demo server from tester's client python ssl module
#
# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.3

CA_TMPL = """\
cn = "Test CA"
ca
cert_signing_key
expiration_days = 3
"""

SERVER_TMPL = """\
cn = "localhost"
dns_name = "localhost"
ip_address = "127.0.0.1"
tls_www_server
signing_key
encryption_key
expiration_days = 2
"""


class ServerInfo(TypedDict):
    host: str
    port: int
    ca: str


@pytest.fixture(scope="module", autouse=True)
def host_with_gnutls_utils(host: Host) -> None:
    for binary in ("certtool", "gnutls-serv"):
        if not host.binary_exists(binary):
            pytest.fail(f"gnutls-utils's {binary} is not installed on the host")


@pytest.fixture(scope="module")
def gnutls_test_path(host: Host) -> Generator[Path, None, None]:
    path: Path | None = None
    try:
        path = Path(host.ssh("mktemp -d"))

        host.create_file(str(path / "ca.tmpl"), CA_TMPL)
        host.create_file(str(path / "server.tmpl"), SERVER_TMPL)

        host.ssh(f"certtool --generate-privkey --outfile {path / 'ca-key.pem'}")
        host.ssh("certtool --generate-self-signed"
                 f" --load-privkey {path / 'ca-key.pem'}"
                 f" --template {path / 'ca.tmpl'} --outfile {path / 'ca.pem'}")
        host.ssh(f"certtool --generate-privkey --outfile {path / 'server-key.pem'}")
        host.ssh("certtool --generate-certificate"
                 f" --load-privkey {path / 'server-key.pem'}"
                 f" --load-ca-certificate {path / 'ca.pem'}"
                 f" --load-ca-privkey {path / 'ca-key.pem'}"
                 f" --template {path / 'server.tmpl'}"
                 f" --outfile {path / 'server.pem'}"
                 )

        yield path
    finally:
        if path is not None:
            host.ssh(f"rm -rf {path}", check=False)


@pytest.fixture(scope="module")
def server(
    host: Host, gnutls_test_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> Generator[ServerInfo, None, None]:
    # Fetch CA cert locally so the tester's ssl module can verify it.
    local_ca = tmp_path_factory.mktemp("ca") / "ca.pem"

    port: int | None = None
    try:
        host.scp(str(gnutls_test_path / "ca.pem"), str(local_ca), local_dest=True)
        port = host.get_free_port()
        host.ssh(f"iptables -I INPUT -p tcp --dport {port} -j ACCEPT")
        host.ssh(f"gnutls-serv --http"
                 f" --port {port}"
                 f" --x509certfile {gnutls_test_path / 'server.pem'}"
                 f" --x509keyfile {gnutls_test_path / 'server-key.pem'}",
                 background=True)
        host.wait_for_port(port)
        yield ServerInfo(host=host.hostname_or_ip, port=port, ca=str(local_ca))
    finally:
        if port is not None:
            # [s] guard: the pattern doesn't match its own shell's command line
            host.ssh(f"pkill -f 'gnutls-[s]erv.*--port {port}'", check=False)
            host.ssh(f"iptables -D INPUT -p tcp --dport {port} -j ACCEPT", check=False)


def _http_request(
    server_info: ServerInfo,
    *,
    cafile: str | None = None,
    minimum_version: ssl.TLSVersion | None = None,
    maximum_version: ssl.TLSVersion | None = None,
) -> tuple[bytes, bytes, str, tuple[str, str, int]]:
    """
    Connect to the gnutls-serv from the tester machine.

    Returns (body, header, tls_version, cipher_tuple).
    Raises ssl.SSLCertVerificationError if certificate verification fails.
    """
    ctx = ssl.create_default_context(cafile=cafile)

    if minimum_version is not None:
        ctx.minimum_version = minimum_version
    if maximum_version is not None:
        ctx.maximum_version = maximum_version

    logging.info(
        f"Connecting to {server_info['host']}:{server_info['port']}"
        f" ([{minimum_version}..{maximum_version}]+{cafile})"
    )
    with (
        socket.create_connection((server_info["host"], server_info["port"]), timeout=5) as sock,
        ctx.wrap_socket(sock, server_hostname="localhost") as tls,
    ):
        version = tls.version()
        cipher = tls.cipher()
        assert version is not None
        assert cipher is not None
        logging.info(f"TLS session established: {version}")
        tls.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
        with tls.makefile("rb") as f:
            header = f.readline()
            assert header.startswith(b"HTTP/1.0 200 OK"), f"unexpected response: {header!r}"
            body = f.read()
        logging.debug(f"Response: {(header + body).decode(errors='replace')}")
        return body, header, version, cipher


def test_http_CA_untrusted(server: ServerInfo) -> None:
    logging.info("Negative test using tester's default CA (without DUT's one): CERTIFICATE_VERIFY_FAILED")
    with pytest.raises(ssl.SSLCertVerificationError):
        _http_request(server)


def test_http_CA_trusted(server: ServerInfo) -> None:
    body, _, version, cipher = _http_request(server, cafile=server["ca"])
    assert version == "TLSv1.3"
    assert cipher[0] == "TLS_AES_256_GCM_SHA384"
    assert cipher[1] == "TLSv1.3"
    assert cipher[2] == 256
    assert b"<TR><TD>Protocol version:</TD><TD>TLS1.3</TD></TR>" in body


def test_http_TLSv1_3(server: ServerInfo) -> None:
    body, _, version, cipher = _http_request(server, cafile=server["ca"],
                                             minimum_version=ssl.TLSVersion.TLSv1_3)
    assert version == "TLSv1.3"
    assert cipher[0] == "TLS_AES_256_GCM_SHA384"
    assert cipher[1] == "TLSv1.3"
    assert cipher[2] == 256
    assert b"<TR><TD>Protocol version:</TD><TD>TLS1.3</TD></TR>" in body


def test_http_TLSv1_2(server: ServerInfo) -> None:
    body, _, version, cipher = _http_request(server, cafile=server["ca"],
                                             minimum_version=ssl.TLSVersion.TLSv1_2,
                                             maximum_version=ssl.TLSVersion.TLSv1_2)
    assert version == "TLSv1.2"
    assert cipher[0] == "ECDHE-RSA-AES256-GCM-SHA384"
    assert cipher[1] == "TLSv1.2"
    assert cipher[2] == 256
    assert b"<TR><TD>Protocol version:</TD><TD>TLS1.2</TD></TR>" in body
