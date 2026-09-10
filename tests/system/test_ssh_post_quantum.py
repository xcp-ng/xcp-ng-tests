import pytest

from lib.commands import ssh_with_result
from lib.host import Host

# Hybrid post-quantum key exchange (ML-KEM, e.g. mlkem768x25519-sha256) is
# new in OpenSSH 9.9p1 (reference build openssh-9.9p1-27.2.xcpng8.3); the
# OpenSSH build shipped before it had no ML-KEM support.
# This test proves it is actually negotiated by default, not just compiled
# in and available on request (see test_ssh_algorithms.py for per-algorithm
# coverage).
#
# Requirements:
# - an XCP-ng host (--hosts) >= 8.3, with openssh >= 9.9p1 installed

def _negotiated_kex_algorithm(host: Host) -> str:
    # multiplexing must be off, otherwise a shared control connection could
    # be reused and its (already negotiated) algorithm silently returned.
    result = ssh_with_result(host.hostname_or_ip, 'true', options=['-v'], multiplexing=False)
    for line in result.ssherr.splitlines():
        if line.startswith('debug1: kex: algorithm:'):
            return line.rsplit(':', 1)[1].strip()
    pytest.fail(f"could not find the negotiated KEX algorithm in ssh -v output:\n{result.ssherr}")

def test_pq_kex_preferred_by_default(host: Host) -> None:
    """ The default KEX negotiated must be the hybrid post-quantum one. """
    algo = _negotiated_kex_algorithm(host)
    assert algo.startswith("mlkem"), f"expected a hybrid post-quantum KEX by default, got {algo}"
