import pytest

from lib.common import Defer
from lib.host import Host

USERNAME = "xcpng_test_sudo"
SUDOERS_D = "/etc/sudoers.d"

@pytest.fixture
def disposable_user(host: Host, defer: Defer) -> str:
    """A disposable, password-locked local user."""
    host.ssh(f"useradd -m -s /bin/bash {USERNAME}")
    defer(lambda: host.ssh(f"userdel -r -f {USERNAME}", check=False))
    return USERNAME

@pytest.fixture
def whoami_sudoer(host: Host, disposable_user: str, defer: Defer) -> str:
    """disposable_user, granted NOPASSWD sudo access to /usr/bin/whoami, and nothing else."""
    dropin = f"{SUDOERS_D}/99-xcpng-test-sudo"
    host.create_file(dropin, f"{disposable_user} ALL=(root) NOPASSWD: /usr/bin/whoami\n")
    defer(lambda: host.ssh(f"rm -f {dropin}"))
    return disposable_user
