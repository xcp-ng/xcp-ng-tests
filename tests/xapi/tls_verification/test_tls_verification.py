import logging
import pytest

from lib.commands import SSHCommandFailed

# Requirements:
# From --hosts parameter:
# - A XCP-ng >= 8.3 pool with at least two hosts

# Context: from XCP-ng 8.3 onwards, each host has an internal TLS certificate identified by SNI
# as "xapi:pool", allowing other pool members to verify its identity.
# When a connection is established between two hosts of the pool, one acting as server, the other as client:
# * The client asks to establish a TLS connection.
# * The server provides its x509 certificate, which is verified by the client.
# * The client authenticates using the pool secret.

XAPI_POOL_PEM_FILENAME = 'xapi-pool-tls.pem'
XAPI_POOL_PEM_FILEPATH = f'/etc/xensource/{XAPI_POOL_PEM_FILENAME}'

@pytest.fixture(scope="module")
def host_with_tls_verification_enabled(hostA1):
    for h in hostA1.pool.hosts:
        logging.info(f"Check that TLS verification is enabled on host {h}")
        assert h.param_get("tls-verification-enabled"), f"TLS verification must be enabled on host {h}"
        logging.info(f"Check that the host certificate exists on host {h}")
        cert_uuid = hostA1.xe('certificate-list', {'host': h.uuid, 'type': 'host_internal'}, minimal=True)
        assert len(cert_uuid) > 0, f"A host_internal certificate must exist on host {h}"


@pytest.mark.usefixtures("host_at_least_8_3", "host_with_tls_verification_enabled")
class TestTLSVerification:
    def _test_tls_verification(self, hostA1, with_toolstack_restart=False):
        for h in hostA1.pool.hosts[1:]:
            logging.info(f"Establish a connexion from host {hostA1} to host {h} by running 'xe host-dmesg'")
            hostA1.xe('host-dmesg', {'host': h.uuid})
            # In theory, when TLS is enabled, the pool members keep an open connection to the host
            # at all times. We'll just check that a XAPI request (via xe) on the pool member works.
            if with_toolstack_restart:
                # Restart toolstack on client host to clear any existing TLS connection
                h.restart_toolstack(True)
            logging.info(f"Test connexion from host {h} to host {hostA1} by running 'xe host-dmesg'")
            h.xe('host-dmesg', {'host': hostA1.uuid})

    def test_tls_verification(self, hostA1):
        self._test_tls_verification(hostA1)

    def test_refresh_certificate(self, hostA1):
        logging.info("Refresh the xapi:pool certificate on every pool member")
        for h in hostA1.pool.hosts:
            old_checksum = h.ssh(['md5sum', XAPI_POOL_PEM_FILEPATH]).split()[0]
            hostA1.xe('host-refresh-server-certificate', {'host': h.uuid})
            new_checksum = h.ssh(['md5sum', XAPI_POOL_PEM_FILEPATH]).split()[0]
            assert old_checksum != new_checksum, "The new certificate must differ from the previous one"
        # Now that we refreshed the certs, check that the connexions still work
        self._test_tls_verification(hostA1, with_toolstack_restart=True)

    @pytest.fixture(scope="function")
    def hostA2_with_saved_cert(self, hostA2):
        tmp_dir = hostA2.ssh(['mktemp', '-d'])
        logging.info(f"Save {XAPI_POOL_PEM_FILEPATH} on {hostA2}")
        hostA2.ssh(['cp', XAPI_POOL_PEM_FILEPATH, tmp_dir])
        yield hostA2
        logging.info(f"Restore {XAPI_POOL_PEM_FILEPATH} on {hostA2}")
        hostA2.ssh(['cp', '-f', f'{tmp_dir}/{XAPI_POOL_PEM_FILENAME}', XAPI_POOL_PEM_FILEPATH])
        hostA2.ssh(['rm', '-r', tmp_dir])
        hostA2.ssh(['systemctl', 'reload-or-restart stunnel@xapi'])

    def test_break_cert(self, hostA1, hostA2_with_saved_cert):
        hostA2 = hostA2_with_saved_cert
        logging.info(f"Replace the certificate on host {hostA2}")
        hostA2.ssh(['rm', XAPI_POOL_PEM_FILEPATH])
        hostA2.ssh(['/opt/xensource/libexec/gencert', XAPI_POOL_PEM_FILEPATH, 'xapi:pool'])
        hostA2.ssh(['systemctl', 'reload-or-restart stunnel@xapi'])
        # Restart toolstack on client host to clear any existing TLS connection
        hostA1.restart_toolstack(True)
        # now the master host should not be able to connect to the pool member anymore
        logging.info(f"Test connexion from host {hostA1} to host {hostA2} by running 'xe host-dmesg'")
        with pytest.raises(SSHCommandFailed) as excinfo:
            hostA1.xe('host-dmesg', {'host': hostA2.uuid})
        assert "You attempted an operation which involves a host which could not be contacted." in excinfo.value.stdout

    def test_toolstack_restart(self, hostA1, hostA2):
        """
        Same test as the previous one, but we don't break the cert.

        Just to rule out a connection error caused by the toolstack restart, in the previous test
        """
        # Restart toolstack on client host to clear any existing TLS connection
        hostA1.restart_toolstack(True)
        logging.info(f"Test connexion from host {hostA1} to host {hostA2} by running 'xe host-dmesg'")
        hostA1.xe('host-dmesg', {'host': hostA2.uuid})
