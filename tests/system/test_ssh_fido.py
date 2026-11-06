import pytest

from lib.common import Defer
from lib.host import Host

# We can't use a real FIDO device in CI, so we test that
# the host accepts FIDO keys when the appropriate drop-in is present.
# Requirements:
# - an XCP-ng host (--hosts) >= 8.3, with OpenSSH >= 9.9p1

PACKAGED_PUBKEY_ALGORITHMS = [
    "ssh-ed25519",
    "ecdsa-sha2-nistp521",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp256",
    "rsa-sha2-512",
    "rsa-sha2-256",
]
SK_PUBKEY_ALGORITHMS = [
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
]

# The full list must be given: drop-ins are included before the packaged
# settings, so a "+sk-..." value would extend OpenSSH's built-in default
# instead of the packaged list.
FIDO_DROPIN = f"PubkeyAcceptedAlgorithms {','.join(PACKAGED_PUBKEY_ALGORITHMS + SK_PUBKEY_ALGORITHMS)}"

def _accepted_pubkey_algorithms(host: Host) -> list[str]:
    for line in host.ssh("sshd -T").splitlines():
        if line.startswith("pubkeyacceptedalgorithms "):
            return line.split(" ", 1)[1].split(",")
    pytest.fail("pubkeyacceptedalgorithms not found in sshd -T output")

def test_fido_enabled_by_dropin(host: Host, defer: Defer) -> None:
    dropin = "/etc/ssh/sshd_config.d/99-xcp-ng-tests-fido.conf"
    host.ssh(f"echo '{FIDO_DROPIN}' > {dropin}")
    defer(lambda: host.ssh(f"rm -f {dropin}"))

    assert _accepted_pubkey_algorithms(host) == PACKAGED_PUBKEY_ALGORITHMS + SK_PUBKEY_ALGORITHMS
