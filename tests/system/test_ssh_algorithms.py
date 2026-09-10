import pytest

from contextlib import nullcontext

from lib.commands import SSHCommandFailed, ssh
from lib.host import Host

# Exhaustive coverage of the KEX/cipher/MAC/host-key algorithms OpenSSH
# supports, hardcoded from `ssh -Q kex|cipher|mac|key` on the reference
# build (openssh-9.9p1-27.2.xcpng8.3). The lists are deliberately not
# queried live: the point of test_*_list_matches_hardcoded is to fail the
# day the compiled-in algorithm support changes, forcing a conscious
# update of this file instead of the change going unnoticed.
#
# Requirements:
# - an XCP-ng host (--hosts) >= 8.3, with the OpenSSH 9.9p1

# algorithm -> enabled by default (sshd -T) on the reference build
KEX_ALGORITHMS = {
    # hybrid post-quantum, new in this OpenSSH update
    "mlkem1024nistp384-sha384": True,
    "mlkem768x25519-sha256": True,
    "mlkem768nistp256-sha256": True,
    "sntrup761x25519-sha512": True,
    "sntrup761x25519-sha512@openssh.com": True,
    # classical, enabled
    "curve25519-sha256": True,
    "curve25519-sha256@libssh.org": True,
    "ecdh-sha2-nistp521": True,
    "ecdh-sha2-nistp384": True,
    "ecdh-sha2-nistp256": True,
    "diffie-hellman-group16-sha512": True,
    "diffie-hellman-group18-sha512": True,
    # compiled in, but disabled
    "diffie-hellman-group1-sha1": False,
    "diffie-hellman-group14-sha1": False,
    "diffie-hellman-group14-sha256": False,
    "diffie-hellman-group-exchange-sha1": False,
    "diffie-hellman-group-exchange-sha256": False,
}

CIPHERS = {
    "chacha20-poly1305@openssh.com": True,
    "aes256-gcm@openssh.com": True,
    "aes128-gcm@openssh.com": True,
    "aes256-ctr": True,
    "aes128-ctr": True,
    "3des-cbc": False,
    "aes128-cbc": False,
    "aes192-cbc": False,
    "aes256-cbc": False,
    "aes192-ctr": False,
}

MACS = {
    "hmac-sha2-512-etm@openssh.com": True,
    "hmac-sha2-256-etm@openssh.com": True,
    "umac-128-etm@openssh.com": True,
    "hmac-sha2-512": True,
    "hmac-sha2-256": True,
    "umac-128@openssh.com": True,
    "hmac-sha1": False,
    "hmac-sha1-96": False,
    "hmac-md5": False,
    "hmac-md5-96": False,
    "umac-64@openssh.com": False,
    "hmac-sha1-etm@openssh.com": False,
    "hmac-sha1-96-etm@openssh.com": False,
    "hmac-md5-etm@openssh.com": False,
    "hmac-md5-96-etm@openssh.com": False,
    "umac-64-etm@openssh.com": False,
}

# Server host identity algorithms actually exercisable end to end: the host
# only carries one host key per type (ed25519, ecdsa on nistp256, rsa), so
# only algorithms with matching key material are forced here. Certificate
# variants would need a CA, and the sk-* types from `ssh -Q key` are not
# host identity keys at all (they're for user authentication via a
# hardware security key, see test_ssh_fido.py) so they don't belong in a
# HostKeyAlgorithms test.
HOSTKEY_ALGORITHMS = {
    "ssh-ed25519": True,
    "ecdsa-sha2-nistp256": True,
    "rsa-sha2-256": True,
    "rsa-sha2-512": True,
    "ssh-rsa": False,  # raw SHA-1 RSA signature, disabled by crypto-policy
}

# Full compiled-in key type support (`ssh -Q key`), used only to detect if
# that list ever drifts (e.g. FIDO/security-key support silently regressing,
# as it did between the previous and this OpenSSH build).
ALL_COMPILED_KEY_TYPES = {
    "ssh-ed25519",
    "ssh-ed25519-cert-v01@openssh.com",
    "sk-ssh-ed25519@openssh.com",
    "sk-ssh-ed25519-cert-v01@openssh.com",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp256-cert-v01@openssh.com",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp384-cert-v01@openssh.com",
    "ecdsa-sha2-nistp521",
    "ecdsa-sha2-nistp521-cert-v01@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
    "sk-ecdsa-sha2-nistp256-cert-v01@openssh.com",
    "ssh-rsa",
    "ssh-rsa-cert-v01@openssh.com",
}

def _assert_list_matches(host: Host, query: str, expected: set) -> None:
    actual = {line for line in host.ssh(f"ssh -Q {query}").splitlines() if line and ' ' not in line}
    assert actual == expected, (
        f"`ssh -Q {query}` no longer matches the hardcoded list in this test file "
        f"(missing={sorted(expected - actual)}, new={sorted(actual - expected)}); "
        "update the hardcoded list after reviewing the change"
    )

def test_kex_algorithms_list_matches_hardcoded(host: Host) -> None:
    _assert_list_matches(host, "kex", set(KEX_ALGORITHMS))

def test_ciphers_list_matches_hardcoded(host: Host) -> None:
    _assert_list_matches(host, "cipher", set(CIPHERS))

def test_macs_list_matches_hardcoded(host: Host) -> None:
    _assert_list_matches(host, "mac", set(MACS))

def test_key_types_list_matches_hardcoded(host: Host) -> None:
    _assert_list_matches(host, "key", ALL_COMPILED_KEY_TYPES)

def _force_algorithm(host: Host, option: str, algo: str, *, extra_options: list[str] = []) -> None:
    # multiplexing must be off: a shared control connection would reuse the
    # algorithm negotiated by whichever call created it, silently ignoring
    # the -o option on every later call.
    ssh(host.hostname_or_ip, 'true', options=['-o', f'{option}={algo}'] + extra_options, multiplexing=False)

# mlkem768nistp256-sha256 and mlkem1024nistp384-sha384 aren't part of
# upstream OpenSSH (see openssh-10.0-mlkem-nist.patch): they're carried by
# RHEL-family builds (RHEL, CentOS, Alma, and this XCP-ng build) for FIPS
# compliance, but not by other builds, including the machine running these
# tests. There's no second RHEL-family host available to interoperate with
# either, so unlike every other algorithm here, these two are exercised in
# loopback on the host itself (which does understand its own names). That
# can't reach an authenticated session (root has no key to log into
# itself), so instead we assert the exact requested algorithm was the one
# negotiated.
SPECIFIC_KEX_NAMES = {"mlkem768nistp256-sha256", "mlkem1024nistp384-sha384"}

def _negotiated_kex_in_loopback(host: Host, algo: str) -> str:
    # the trailing "; true" keeps the remote command's exit code at 0
    # (the inner loopback ssh fails at authentication, not at key exchange)
    # so we can just inspect its output instead of juggling SSHCommandFailed.
    output = host.ssh(
        "ssh -v -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ControlMaster=no -o KexAlgorithms={algo} localhost true 2>&1; true"
    )
    for line in output.splitlines():
        if line.startswith("debug1: kex: algorithm:"):
            return line.rsplit(':', 1)[1].strip()
    pytest.fail(f"could not find the negotiated KEX algorithm in loopback ssh -v output:\n{output}")

@pytest.mark.parametrize("algo,enabled", KEX_ALGORITHMS.items(), ids=list(KEX_ALGORITHMS))
def test_kex_algorithm(host: Host, algo: str, enabled: bool) -> None:
    if algo in SPECIFIC_KEX_NAMES:
        assert enabled
        assert _negotiated_kex_in_loopback(host, algo) == algo
        return
    with pytest.raises(SSHCommandFailed) if not enabled else nullcontext():
        _force_algorithm(host, "KexAlgorithms", algo)

@pytest.mark.parametrize("algo,enabled", CIPHERS.items(), ids=list(CIPHERS))
def test_cipher_algorithm(host: Host, algo: str, enabled: bool) -> None:
    with pytest.raises(SSHCommandFailed) if not enabled else nullcontext():
        _force_algorithm(host, "Ciphers", algo)

@pytest.mark.parametrize("algo,enabled", MACS.items(), ids=list(MACS))
def test_mac_algorithm(host: Host, algo: str, enabled: bool) -> None:
    # MACs are only negotiated for non-AEAD ciphers (AEAD ciphers like the
    # default chacha20-poly1305/aes-gcm have their MAC built in, making the
    # MACs option moot), so a non-AEAD cipher is pinned to force the point.
    non_aead_cipher = ['-o', 'Ciphers=aes256-ctr']
    with pytest.raises(SSHCommandFailed) if not enabled else nullcontext():
        _force_algorithm(host, "MACs", algo, extra_options=non_aead_cipher)

@pytest.mark.parametrize("algo,enabled", HOSTKEY_ALGORITHMS.items(), ids=list(HOSTKEY_ALGORITHMS))
def test_hostkey_algorithm(host: Host, algo: str, enabled: bool) -> None:
    with pytest.raises(SSHCommandFailed) if not enabled else nullcontext():
        _force_algorithm(host, "HostKeyAlgorithms", algo)
