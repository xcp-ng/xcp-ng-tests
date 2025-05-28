import pytest

# Explicitly import package-scoped fixtures (see explanation in pkgfixtures.py)
from pkgfixtures import host_with_saved_yum_state

TEST_USER = "sasltest"
TEST_PASS = "sasltestpass"

@pytest.fixture(scope="package")
def host_with_cyrus_sasl(host_with_saved_yum_state):
    host = host_with_saved_yum_state
    # Installing cyrus-sasl also installs all required dependencies
    host.yum_install(['cyrus-sasl'])

@pytest.fixture(scope="package")
def create_user_cyrus_sasl(host):
    # Check if the user already exists
    user_exists = host.ssh(['id', TEST_USER], check=False)
    if TEST_USER not in user_exists:
        # Create the user if it does not exist
        host.ssh(['useradd', TEST_USER])
        host.ssh(['sh', '-c', f"echo '{TEST_USER}:{TEST_PASS}' | chpasswd"])
    print(f"User {TEST_USER} is ready for testing.")

@pytest.fixture(scope="package")
def setup_pam_file(host):
    pam_path = "/etc/pam.d/saslauthd"
    content = """
    auth       include      system-auth
    account    include      system-auth
    password   include      system-auth
    session    include      system-auth
    """
    host.ssh([f"echo -e '{content}' > {pam_path}"])
