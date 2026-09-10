import pytest

from lib.commands import SSHCommandFailed
from lib.common import Defer
from lib.host import Host

# FIDO/security-key (sk-*) support is new in this OpenSSH update.
#
# We have no physical FIDO token to enroll a real sk key end to end, so
# these tests prove the feature is compiled in and wired up as far as
# possible without hardware:
# - the sk-* algorithms are advertised by the client and accepted by sshd
#   for pubkey authentication;
# - `ssh-keygen -t ed25519-sk` reaches actual hardware detection and fails
#   with "device not found" rather than "unknown key type", proving the
#   key type itself is recognized and libfido2 support is wired in.
#
# Requirements:
# - an XCP-ng host (--hosts) >= 8.3, with the OpenSSH 9.9p1

SK_KEY_TYPES = {
    "sk-ssh-ed25519@openssh.com",
    "sk-ssh-ed25519-cert-v01@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
    "sk-ecdsa-sha2-nistp256-cert-v01@openssh.com",
}

def test_fido_key_types_advertised_by_client(host: Host) -> None:
    supported = set(host.ssh("ssh -Q key").splitlines())
    missing = SK_KEY_TYPES - supported
    assert not missing, f"ssh client doesn't advertise expected FIDO key types: {missing}"

def test_fido_key_types_accepted_by_server(host: Host) -> None:
    output = host.ssh("sshd -T | grep '^pubkeyacceptedalgorithms '")
    # look for the matching line specifically: some hosts print unrelated
    # lines on every ssh session (e.g. a root-login warning) ahead of it
    line = next(line for line in output.splitlines() if line.startswith("pubkeyacceptedalgorithms "))
    accepted = set(line.split(" ", 1)[1].split(','))
    missing = {"sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com"} - accepted
    assert not missing, f"sshd doesn't accept expected FIDO key types for pubkey auth: {missing}"

def test_sk_key_enrollment_reaches_hardware_detection(host: Host, defer: Defer) -> None:
    """ Without a token, enrollment must fail at the hardware-detection stage, not earlier. """
    keyfile = host.ssh("mktemp -u")
    defer(lambda: host.ssh(f"rm -f {keyfile} {keyfile}.pub"))

    with pytest.raises(SSHCommandFailed) as exc_info:
        host.ssh(f"timeout 8 ssh-keygen -t ed25519-sk -f {keyfile} -N '' -O no-touch-required")

    error = str(exc_info.value)
    assert "device not found" in error, f"expected a hardware-detection failure, got: {error}"
    assert "unknown key type" not in error
