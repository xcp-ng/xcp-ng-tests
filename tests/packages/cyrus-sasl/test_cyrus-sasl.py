import pytest
from lib.commands import SSHCommandFailed

TEST_USER = "sasltest"
TEST_PASS = "sasltestpass"

@pytest.mark.usefixtures("host_with_cyrus_sasl")
@pytest.mark.usefixtures("create_user_cyrus_sasl")
@pytest.mark.usefixtures("setup_pam_file")
class TestsCyrusSasl:
    def test_auth_success(self, host):
        # This auths should succeed
        host.ssh(['systemctl', 'enable', '--now', 'saslauthd.service'])
        host.ssh(['systemctl', 'start', 'saslauthd.service'])
        result = host.ssh(f"testsaslauthd -u {TEST_USER} -p {TEST_PASS} -s saslauthd")
        assert "OK" in result

    def test_auth_failure(self, host):
        # This auth should fail
        host.ssh(['systemctl', 'enable', '--now', 'saslauthd.service'])
        host.ssh(['systemctl', 'start', 'saslauthd.service'])
        with pytest.raises(SSHCommandFailed) as excinfo:
            host.ssh(f"testsaslauthd -u {TEST_USER} -p wrongpassword -s saslauthd")
        assert(
            "NO" in excinfo.value.stdout
        ), f"Expected authentication failure, but got: {excinfo.value.stdout}"
