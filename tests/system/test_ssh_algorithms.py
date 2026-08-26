import pytest

import tempfile
from pathlib import Path

from lib.commands import SSHCommandFailed, local_cmd, ssh, ssh_with_result
from lib.host import Host

from typing import Iterator

# Requirements:
# - an XCP-ng host (--hosts) >= 8.3, with OpenSSH >= 9.9p1

# algorithm -> accepted by the host
KEX_ALGORITHMS = {
    "mlkem768x25519-sha256": True,
    "sntrup761x25519-sha512": True,
    "sntrup761x25519-sha512@openssh.com": True,
    "curve25519-sha256": True,
    "curve25519-sha256@libssh.org": True,
    "ecdh-sha2-nistp521": True,
    "ecdh-sha2-nistp384": True,
    "ecdh-sha2-nistp256": True,
    "diffie-hellman-group16-sha512": True,
    "diffie-hellman-group18-sha512": True,
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
    "umac-128@openssh.com": False,
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

HOSTKEY_ALGORITHMS = {
    "ssh-ed25519": True,
    "ecdsa-sha2-nistp256": True,
    "rsa-sha2-256": True,
    "rsa-sha2-512": True,
    "ssh-rsa": False,
}

# algorithm -> accepted by the host
PUBKEY_ALGORITHMS = {
    "ssh-ed25519": True,
    "ecdsa-sha2-nistp521": True,
    "ecdsa-sha2-nistp384": True,
    "ecdsa-sha2-nistp256": True,
    "rsa-sha2-512": True,
    "rsa-sha2-256": True,
    "ssh-rsa": False,
}

# algorithm -> ssh-keygen options of a matching user key
USER_KEYS = {
    "ssh-ed25519": ["-t", "ed25519"],
    "ecdsa-sha2-nistp521": ["-t", "ecdsa", "-b", "521"],
    "ecdsa-sha2-nistp384": ["-t", "ecdsa", "-b", "384"],
    "ecdsa-sha2-nistp256": ["-t", "ecdsa", "-b", "256"],
    "rsa-sha2-512": ["-t", "rsa"],
    "rsa-sha2-256": ["-t", "rsa"],
    "ssh-rsa": ["-t", "rsa"],
}

def _can_connect(host: Host, options: list[str]) -> bool:
    try:
        # without multiplexing, as an existing master connection would ignore the options
        ssh(host.hostname_or_ip, 'true', options=options, multiplexing=False)
    except SSHCommandFailed:
        return False
    return True

@pytest.fixture(scope="module")
def user_keys(host: Host) -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as keydir:
        for algo, keygen_opts in USER_KEYS.items():
            local_cmd(['ssh-keygen', *keygen_opts, '-q', '-N', '', '-C', f'xcp-ng-ci_test_{algo}',
                       '-f', f'{keydir}/xcp-ng-ci_test_{algo}'])
        pubkeys = ''.join(pub.read_text() for pub in Path(keydir).glob('*.pub'))
        host.ssh(f"echo -n '{pubkeys}' >> ~/.ssh/authorized_keys")
        yield Path(keydir)
        host.ssh("sed -i '/ xcp-ng-ci_test_/d' ~/.ssh/authorized_keys")

@pytest.mark.parametrize("algo,accepted", KEX_ALGORITHMS.items(), ids=list(KEX_ALGORITHMS))
def test_kex_algorithm(host: Host, algo: str, accepted: bool) -> None:
    assert _can_connect(host, ['-o', f'KexAlgorithms={algo}']) == accepted

@pytest.mark.parametrize("algo,accepted", CIPHERS.items(), ids=list(CIPHERS))
def test_cipher(host: Host, algo: str, accepted: bool) -> None:
    assert _can_connect(host, ['-o', f'Ciphers={algo}']) == accepted

@pytest.mark.parametrize("algo,accepted", MACS.items(), ids=list(MACS))
def test_mac(host: Host, algo: str, accepted: bool) -> None:
    # MACs are only negotiated with a non-AEAD cipher
    assert _can_connect(host, ['-o', 'Ciphers=aes256-ctr', '-o', f'MACs={algo}']) == accepted

@pytest.mark.parametrize("algo,accepted", HOSTKEY_ALGORITHMS.items(), ids=list(HOSTKEY_ALGORITHMS))
def test_hostkey_algorithm(host: Host, algo: str, accepted: bool) -> None:
    assert _can_connect(host, ['-o', f'HostKeyAlgorithms={algo}']) == accepted

@pytest.mark.parametrize("algo,accepted", PUBKEY_ALGORITHMS.items(), ids=list(PUBKEY_ALGORITHMS))
def test_pubkey_algorithm(host: Host, user_keys: Path, algo: str, accepted: bool) -> None:
    # the client also restricts host key algorithms to this list, so one matching a host key must remain
    options = ['-i', str(user_keys / f"xcp-ng-ci_test_{algo}"), '-o', 'IdentitiesOnly=yes',
               '-o', f'PubkeyAcceptedAlgorithms={algo},ssh-ed25519']
    assert _can_connect(host, options) == accepted

def test_pq_kex_preferred_by_default(host: Host) -> None:
    # without multiplexing, as an existing master connection would already be negotiated
    result = ssh_with_result(host.hostname_or_ip, 'true', options=['-v'], multiplexing=False)
    for line in result.ssherr.splitlines():
        if line.startswith('debug1: kex: algorithm:'):
            algo = line.rsplit(':', 1)[1].strip()
            assert algo.startswith("mlkem"), f"expected a hybrid post-quantum KEX by default, got {algo}"
            return
    pytest.fail(f"could not find the negotiated KEX algorithm in ssh -v output:\n{result.ssherr}")
