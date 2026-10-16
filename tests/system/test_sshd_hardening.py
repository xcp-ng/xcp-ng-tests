import pytest

from lib.commands import SSHCommandFailed, ssh
from lib.host import Host

# Checks the hardening directives of the packaged XCP-ng sshd_config.
# Requirements:
# - an XCP-ng host (--hosts) >= 8.3, with OpenSSH >= 9.9p1

# sshd -T keyword -> expected value
HARDENING_SETTINGS = {
    "logingracetime": "60",
    "maxauthtries": "3",
    "permitemptypasswords": "no",
    "kbdinteractiveauthentication": "no",
    "gssapiauthentication": "no",
    "gssapicleanupcredentials": "no",
    "x11forwarding": "no",
    "disableforwarding": "yes",
    "authorizedkeysfile": ".ssh/authorized_keys",
}

@pytest.fixture(scope="module")
def sshd_effective_config(host: Host) -> dict[str, str]:
    config = {}
    for line in host.ssh("sshd -T").splitlines():
        key, _, value = line.partition(" ")
        config[key] = value
    return config

@pytest.mark.parametrize("keyword,expected", HARDENING_SETTINGS.items(), ids=list(HARDENING_SETTINGS))
def test_hardening_setting(sshd_effective_config: dict[str, str], keyword: str, expected: str) -> None:
    assert sshd_effective_config.get(keyword) == expected

def test_remote_forwarding_refused(host: Host) -> None:
    # remote forwardings are requested before the session starts, so the refusal
    # makes ssh exit before running the command
    options = ['-o', 'ExitOnForwardFailure=yes', '-R', '0:localhost:22']
    # without multiplexing, as an existing master connection would ignore the options
    with pytest.raises(SSHCommandFailed):
        ssh(host.hostname_or_ip, 'true', options=options, multiplexing=False)
