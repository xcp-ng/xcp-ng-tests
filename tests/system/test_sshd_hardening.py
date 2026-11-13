import pytest

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
    # only local TCP forwarding (ssh -L) is allowed...
    "disableforwarding": "no",
    "allowtcpforwarding": "local",
    "allowagentforwarding": "no",
    "allowstreamlocalforwarding": "no",
    # ... and only towards the host loopback
    "permitopen": "localhost:* 127.0.0.1:* [::1]:*",
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
