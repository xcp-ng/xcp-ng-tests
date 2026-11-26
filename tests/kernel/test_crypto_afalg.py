from __future__ import annotations

import pytest

from .utils import KernelChecks

# Requirements:
# From --hosts parameter:
# - host(A1): any XCP-ng host with python3 in dom0.
#
# Userspace crypto (AF_ALG) regression tests, run in dom0. They were written after the CopyFail
# (CVE-2026-31431) backports: XenServer's 8.0.49 broke af_alg_sendmsg() for concurrent and chunked requests, and
# the switch of algif_aead to out-of-place operation exposed a NULL dereference in aesni-intel for AAD-only
# requests, which an unprivileged user could escalate into a dom0 panic through AIO.
#
# If one of these tests triggers a kernel oops, the no_kernel_errors fixture makes it fail even when the check
# itself didn't notice anything.

def test_known_answers(kernel_checks: KernelChecks) -> None:
    kernel_checks.run('afalg-kat')

@pytest.mark.parametrize('alg', [
    'gcm(aes)',
    'rfc4106(gcm(aes))',
    'rfc4543(gcm(aes))',
    'ccm(aes)',
    'authenc(hmac(sha256),cbc(aes))',
    'rfc7539(chacha20,poly1305)',
    'authencesn(hmac(sha256),cbc(aes))',
])
def test_aead_empty_plaintext(kernel_checks: KernelChecks, alg: str) -> None:
    """AAD-only requests: the aesni GCM variants used to oops on them."""
    info = kernel_checks.run('aead-empty', f"'{alg}'")
    if f"{alg} not available" in info:
        pytest.skip(f"{alg} is not available on {kernel_checks.host}")

def test_chunked_requests(kernel_checks: KernelChecks) -> None:
    """skcipher, AEAD and hash requests sent in random chunks (MSG_MORE) give the same result as one-shot ones."""
    kernel_checks.run('afalg-multipart', 8)

def test_splice_does_not_modify_page_cache(kernel_checks: KernelChecks) -> None:
    """CopyFail: data spliced from the page cache into AF_ALG must never be written to."""
    kernel_checks.run('afalg-splice', kernel_checks.workdir, 6)

def test_concurrency(kernel_checks: KernelChecks) -> None:
    """No spurious errors on many short-lived requests, and sane behaviour with concurrent writers."""
    kernel_checks.run('afalg-concurrency', 3000)
