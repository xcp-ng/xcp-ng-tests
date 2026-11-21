#!/usr/bin/env python3
# Annotations must be evaluable by python 3.6: builtin generics like list[int] are not.
# ruff: noqa: UP006
"""
Kernel regression checks, run inside dom0 by tests/kernel.

This file is copied to the host and executed there with the dom0 python3 (3.6 on XCP-ng 8.3), so it must stay
compatible with it: no `from __future__ import annotations`, no subscripted generics in annotations, no f-strings
with `=`, ...

Each subcommand prints a single JSON object on stdout: {"failures": [...], "info": [...]}. A non-empty "failures"
list means the check failed. If the process dies (for instance after a kernel oops), there is no JSON at all.
"""

import errno
import hashlib
import hmac
import json
import os
import random
import socket
import struct
import sys
import tempfile
import threading
import time

from typing import Any, Dict, List, Tuple

SOL_ALG = 279
ALG_SET_KEY = 1
ALG_SET_AEAD_AUTHSIZE = 5
TCP_FASTOPEN = 23
MSG_FASTOPEN = 0x20000000

FAILURES = []  # type: List[str]
INFO = []  # type: List[Any]
RND = random.Random(0xC0FFEE)

def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILURES.append(msg)

# ---------------------------------------------------------------------------------------------------------------------
# AF_ALG helpers
# ---------------------------------------------------------------------------------------------------------------------

def tfm(typ: str, name: str, key: bytes = b'', authsize: int = 0) -> socket.socket:
    s = socket.socket(socket.AF_ALG, socket.SOCK_SEQPACKET, 0)
    s.bind((typ, name))
    if key:
        s.setsockopt(SOL_ALG, ALG_SET_KEY, key)
    if authsize:
        s.setsockopt(SOL_ALG, ALG_SET_AEAD_AUTHSIZE, None, authsize)
    return s

def alg_available(typ: str, name: str) -> bool:
    try:
        tfm(typ, name).close()
        return True
    except OSError:
        return False

def recv_all(op: socket.socket, n: int) -> bytes:
    # A zero-length recv() never starts the crypto operation (aead_recvmsg loops on msg_data_left()), so always ask
    # for at least one byte: the kernel then returns the (possibly empty) output, or the operation's error.
    out = b""
    while True:
        d = op.recv(max(n - len(out), 1))
        out += d
        if not d or len(out) >= n:
            return out

def authenc_key(enckey: bytes, authkey: bytes) -> bytes:
    # struct rtattr {len=8, type=CRYPTO_AUTHENC_KEYA_PARAM} + __be32 enckeylen, then the keys
    return struct.pack("=HH", 8, 1) + struct.pack(">I", len(enckey)) + authkey + enckey

def split(n: int, maxparts: int = 12) -> List[int]:
    """Random chunk sizes summing up to n."""
    parts = []
    while n > 0:
        max_chunk_size = max(1, min(n, RND.choice([1, 7, 15, 16, 100, 4096, 5000, 70000])))
        c = RND.randint(1, max_chunk_size)
        parts.append(c)
        n -= c
        if len(parts) >= maxparts - 1 and n:
            parts.append(n)
            break
    return parts

def skcipher_op(t: socket.socket, iv: bytes, data: bytes, encrypt: bool, chunks: List[int]) -> bytes:
    op, _ = t.accept()
    o = socket.ALG_OP_ENCRYPT if encrypt else socket.ALG_OP_DECRYPT
    pos = 0
    for i, c in enumerate(chunks):
        more = socket.MSG_MORE if i < len(chunks) - 1 else 0
        piece = data[pos:pos + c]
        pos += c
        if i == 0:
            op.sendmsg_afalg([piece], op=o, iv=iv, flags=more)
        else:
            op.sendmsg([piece], [], more)
    try:
        return recv_all(op, len(data))
    finally:
        op.close()

def aead_op(t: socket.socket, encrypt: bool, iv: bytes, assoc: bytes, data: bytes, outlen: int,
            chunks: List[int] = []) -> bytes:
    """Run one AEAD request, data being sent in chunks (with MSG_MORE). Returns assoc || output."""
    op, _ = t.accept()
    o = socket.ALG_OP_ENCRYPT if encrypt else socket.ALG_OP_DECRYPT
    buf = assoc + data
    chunks = chunks or [len(buf)]
    pos = 0
    for i, c in enumerate(chunks):
        more = socket.MSG_MORE if i < len(chunks) - 1 else 0
        piece = buf[pos:pos + c]
        pos += c
        if i == 0:
            op.sendmsg_afalg([piece], op=o, iv=iv, assoclen=len(assoc), flags=more)
        else:
            op.sendmsg([piece], [], more)
    try:
        return recv_all(op, len(assoc) + outlen)
    finally:
        op.close()

def ref_cbc(key: bytes, iv: bytes, pt: bytes) -> bytes:
    """CBC built on single-block ecb(aes) requests, as an independent reference."""
    t = tfm("skcipher", "ecb(aes)", key)
    out, prev = b"", iv
    for i in range(0, len(pt), 16):
        x = bytes(a ^ b for a, b in zip(pt[i:i + 16], prev))
        op, _ = t.accept()
        op.sendmsg_afalg([x], op=socket.ALG_OP_ENCRYPT)
        prev = op.recv(16)
        op.close()
        out += prev
    t.close()
    return out

class Aead:
    def __init__(self, name: str, keylen: int, ivlen: int, authsize: int, block: int = 1, min_assoc: int = 0,
                 assoc_sizes: Tuple[int, ...] = (0, 8, 13, 16, 20, 64)) -> None:
        self.name = name
        self.keylen = keylen
        self.ivlen = ivlen
        self.authsize = authsize
        self.block = block
        self.assoc_sizes = tuple(a for a in assoc_sizes if a >= min_assoc)

    def key(self) -> bytes:
        if self.name.startswith("authenc"):
            return authenc_key(os.urandom(16), os.urandom(32))
        return os.urandom(self.keylen)

    def iv(self) -> bytes:
        if self.name.startswith("ccm"):
            return bytes([3]) + os.urandom(11) + bytes(4)  # L' = 3
        return os.urandom(self.ivlen)

AEADS = {
    "gcm(aes)": Aead("gcm(aes)", 16, 12, 16),
    "rfc4106(gcm(aes))": Aead("rfc4106(gcm(aes))", 20, 8, 16, assoc_sizes=(16, 20)),
    "rfc4543(gcm(aes))": Aead("rfc4543(gcm(aes))", 20, 8, 16, assoc_sizes=(16, 20)),
    "ccm(aes)": Aead("ccm(aes)", 16, 16, 16),
    "authenc(hmac(sha256),cbc(aes))": Aead("authenc(hmac(sha256),cbc(aes))", 0, 16, 32, block=16),
    "authenc(hmac(sha1),cbc(aes))": Aead("authenc(hmac(sha1),cbc(aes))", 0, 16, 12, block=16),
    "rfc7539(chacha20,poly1305)": Aead("rfc7539(chacha20,poly1305)", 32, 12, 16),
    "authencesn(hmac(sha256),cbc(aes))": Aead("authencesn(hmac(sha256),cbc(aes))", 0, 16, 32, block=16,
                                              min_assoc=8),
}

# Generic (non-accelerated) implementations, used as a reference for the accelerated ones
GENERIC_GCM = "gcm_base(ctr(aes-generic),ghash-generic)"
GENERIC = {
    "gcm(aes)": GENERIC_GCM,
    "rfc4106(gcm(aes))": "rfc4106(%s)" % GENERIC_GCM,
    "rfc4543(gcm(aes))": "rfc4543(%s)" % GENERIC_GCM,
}

# ---------------------------------------------------------------------------------------------------------------------
# AF_ALG checks
# ---------------------------------------------------------------------------------------------------------------------

def cmd_afalg_kat() -> None:
    """Known answers: NIST GCM test case 4, and authenc against a python reference."""
    key = bytes.fromhex("feffe9928665731c6d6a8f9467308308")
    iv = bytes.fromhex("cafebabefacedbaddecaf888")
    pt = bytes.fromhex("d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72"
                       "1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b39")
    aad = bytes.fromhex("feedfacedeadbeeffeedfacedeadbeefabaddad2")
    exp = bytes.fromhex("42831ec2217774244b7221b784d0d49ce3aa212f2c02a4e035c17e2329aca12e"
                        "21d514b25466931c7d8f6a5aac84aa051ba30b396a0aac973d58e091"
                        "5bc94fbc3221a5db94fae95ae7121a47")
    for name in ("gcm(aes)", GENERIC_GCM):
        t = tfm("aead", name, key, 16)
        out = aead_op(t, True, iv, aad, pt, len(pt) + 16)
        check(out[len(aad):] == exp, "%s: NIST GCM test case 4 encryption mismatch" % name)
        out = aead_op(t, False, iv, aad, exp, len(pt))
        check(out[len(aad):len(aad) + len(pt)] == pt, "%s: NIST GCM test case 4 decryption mismatch" % name)
        t.close()

    for it in range(10):
        ek, ak = os.urandom(16), os.urandom(32)
        iv = os.urandom(16)
        assoc = os.urandom(RND.choice([0, 8, 16]))
        pt = os.urandom(16 * RND.randint(0, 100))
        t = tfm("aead", "authenc(hmac(sha256),cbc(aes))", authenc_key(ek, ak), 32)
        out = aead_op(t, True, iv, assoc, pt, len(pt) + 32)[len(assoc):]
        t.close()
        ct = ref_cbc(ek, iv, pt)
        tag = hmac.new(ak, assoc + ct, hashlib.sha256).digest()
        check(out == ct + tag, "authenc(hmac(sha256),cbc(aes)): mismatch with python reference (iteration %d)" % it)

def cmd_aead_empty(name: str) -> None:
    """
    AAD with an empty plaintext.

    With an out-of-place algif_aead, the source scatterlist ends right after the AAD: aesni-intel's
    gcmaes_crypt_by_sg() used to dereference NULL there (missing 3af349639597 "crypto: x86/aesni-gcm - fix crash
    on empty plaintext").
    """
    a = AEADS[name]
    if not alg_available("aead", name):
        INFO.append("%s not available" % name)
        return
    key, iv = a.key(), a.iv()
    for alen in sorted(set(a.assoc_sizes + (16,))):
        if alen < 8 and name.startswith("authencesn"):
            continue
        assoc = os.urandom(alen)
        t = tfm("aead", name, key, a.authsize)
        out = aead_op(t, True, iv, assoc, b"", a.authsize)
        check(len(out) == alen + a.authsize, "%s: AAD-only encryption returned %d bytes, expected %d"
              % (name, len(out), alen + a.authsize))
        tag = out[alen:]
        dec = aead_op(t, False, iv, assoc, tag, 0)
        check(dec[:alen] == assoc, "%s: AAD-only decryption failed (assoclen %d)" % (name, alen))
        bad = bytearray(tag)
        bad[0] ^= 1
        try:
            aead_op(t, False, iv, assoc, bytes(bad), 0)
            check(False, "%s: AAD-only decryption accepted a bad tag (assoclen %d)" % (name, alen))
        except OSError as e:
            check(e.errno == errno.EBADMSG, "%s: bad tag gave %s instead of EBADMSG" % (name, e))
        t.close()
        if name in GENERIC:
            g = tfm("aead", GENERIC[name], key, a.authsize)
            ref = aead_op(g, True, iv, assoc, b"", a.authsize)[alen:]
            g.close()
            check(tag == ref, "%s: AAD-only tag differs from %s (assoclen %d)" % (name, GENERIC[name], alen))

def cmd_afalg_multipart(iterations: int) -> None:
    """Requests split in random chunks with MSG_MORE must give the same result as one-shot requests."""
    for name, mode_bs in (("cbc(aes)", 16), ("ctr(aes)", 1)):
        for it in range(iterations):
            key, iv = os.urandom(RND.choice([16, 24, 32])), os.urandom(16)
            n = RND.randint(1, 200000)
            n -= n % mode_bs
            pt = os.urandom(n)
            t = tfm("skcipher", name, key)
            one = skcipher_op(t, iv, pt, True, [n])
            if it < 3 and name == "cbc(aes)":
                check(one == ref_cbc(key, iv, pt), "%s: one-shot mismatch with the ecb reference" % name)
            check(skcipher_op(t, iv, pt, True, split(n)) == one, "%s: chunked encryption mismatch (n=%d)" % (name, n))
            check(skcipher_op(t, iv, one, False, split(n)) == pt, "%s: chunked decryption mismatch (n=%d)" % (name, n))
            t.close()

    for name, a in AEADS.items():
        if not alg_available("aead", name):
            INFO.append("%s not available" % name)
            continue
        for it in range(iterations):
            t = tfm("aead", name, a.key(), a.authsize)
            iv = a.iv()
            alen = RND.choice(a.assoc_sizes)
            n = RND.choice([0, RND.randint(1, 100000)])
            n -= n % a.block
            assoc, pt = os.urandom(alen), os.urandom(n)
            ct1 = aead_op(t, True, iv, assoc, pt, n + a.authsize)
            check(len(ct1) == alen + n + a.authsize, "%s: encryption output length %d, expected %d"
                  % (name, len(ct1), alen + n + a.authsize))
            check(ct1[:alen] == assoc, "%s: AAD not copied to the output" % name)
            ct2 = aead_op(t, True, iv, assoc, pt, n + a.authsize, split(alen + n))
            check(ct1 == ct2, "%s: chunked encryption mismatch (assoclen=%d n=%d)" % (name, alen, n))
            ct = ct1[alen:]
            dec = aead_op(t, False, iv, assoc, ct, n, split(alen + n + a.authsize))
            check(dec[alen:alen + n] == pt, "%s: chunked decryption mismatch (assoclen=%d n=%d)" % (name, alen, n))
            bad = bytearray(ct)
            bad[RND.randrange(len(bad))] ^= 1
            try:
                aead_op(t, False, iv, assoc, bytes(bad), n)
                check(False, "%s: tampered ciphertext accepted (assoclen=%d n=%d)" % (name, alen, n))
            except OSError as e:
                check(e.errno == errno.EBADMSG, "%s: tampered ciphertext gave %s instead of EBADMSG" % (name, e))
            t.close()

    for name, ref in (("sha256", hashlib.sha256), ("sha1", hashlib.sha1), ("sha512", hashlib.sha512)):
        t = tfm("hash", name)
        for it in range(iterations):
            data = os.urandom(RND.randint(0, 300000))
            op, _ = t.accept()
            pos = 0
            for c in split(len(data)):
                op.sendmsg([data[pos:pos + c]], [], socket.MSG_MORE)
                pos += c
            op.send(b"")
            d = op.recv(64)
            op.close()
            check(d == ref(data).digest(), "hash %s: chunked digest mismatch (n=%d)" % (name, len(data)))
        t.close()

def new_file(directory: str, data: bytes) -> str:
    fd, path = tempfile.mkstemp(dir=directory)
    os.write(fd, data)
    os.fsync(fd)
    os.close(fd)
    return path

def read_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()

def sendfile_all(op: socket.socket, path: str) -> None:
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        sent = 0
        while sent < size:
            sent += os.sendfile(op.fileno(), f.fileno(), sent, size - sent)

def cmd_afalg_splice(directory: str, iterations: int) -> None:
    """
    Feed page-cache pages to AF_ALG with sendfile() (splice), and check they are not modified.

    This is the shape of CopyFail (CVE-2026-31431): an in-place algif_aead decryption with authencesn wrote
    past the destination, into the spliced page-cache pages of a file the user could only read.
    """
    for it in range(iterations):
        n = 16 * RND.randint(1, 8000)
        data = os.urandom(n)
        path = new_file(directory, data)
        try:
            key, iv = os.urandom(16), os.urandom(16)
            t = tfm("skcipher", "cbc(aes)", key)
            ref = skcipher_op(t, iv, data, True, [n])
            op, _ = t.accept()
            op.sendmsg_afalg([], op=socket.ALG_OP_ENCRYPT, iv=iv, flags=socket.MSG_MORE)
            # the last splice()d chunk is sent without MSG_MORE, which completes the request
            sendfile_all(op, path)
            out = recv_all(op, n)
            op.close()
            t.close()
            check(out == ref, "cbc(aes): encryption of sendfile()d data mismatch (n=%d)" % n)
            check(read_file(path) == data, "cbc(aes): PAGE CACHE MODIFIED by sendfile() encryption")
        finally:
            os.unlink(path)

        for name in ("gcm(aes)", "authenc(hmac(sha256),cbc(aes))", "authencesn(hmac(sha256),cbc(aes))"):
            a = AEADS[name]
            if not alg_available("aead", name):
                if it == 0:
                    INFO.append("%s not available" % name)
                continue
            t = tfm("aead", name, a.key(), a.authsize)
            aiv = a.iv()
            assoc = os.urandom(16)
            ct = aead_op(t, True, aiv, assoc, data, n + a.authsize)
            for tamper in (False, True):
                blob = bytearray(ct)
                if tamper:
                    blob[-1] ^= 1
                cpath = new_file(directory, bytes(blob))
                try:
                    op, _ = t.accept()
                    op.sendmsg_afalg([], op=socket.ALG_OP_DECRYPT, iv=aiv, assoclen=16, flags=socket.MSG_MORE)
                    sendfile_all(op, cpath)
                    try:
                        pt = recv_all(op, 16 + n)
                        check(not tamper, "%s: tampered sendfile()d ciphertext accepted" % name)
                        check(pt[16:16 + n] == data, "%s: decryption of sendfile()d data mismatch" % name)
                    except OSError as e:
                        check(tamper and e.errno == errno.EBADMSG,
                              "%s: decryption of sendfile()d data failed: %s" % (name, e))
                    op.close()
                    check(read_file(cpath) == bytes(blob),
                          "%s: PAGE CACHE MODIFIED by sendfile() decryption (tampered=%s)" % (name, tamper))
                finally:
                    os.unlink(cpath)
            t.close()

def cmd_afalg_concurrency(sockets: int) -> None:
    """
    Many short-lived requests, and concurrent writers on a single request socket.

    The 8.0.49 backport of "crypto: af_alg - Disallow concurrent writes in af_alg_sendmsg" made sendmsg() return
    spurious errors: requests on fresh sockets must never fail, and concurrent writers may only get EBUSY/EAGAIN.
    """
    key = os.urandom(16)
    aead = tfm("aead", "gcm(aes)", key, 16)
    sk = tfm("skcipher", "cbc(aes)", key)
    errs = {}  # type: Dict[Any, int]
    for i in range(sockets):
        try:
            if i & 1:
                aead_op(aead, True, os.urandom(12), b"a" * 8, b"x" * 64, 64 + 16, [8, 64] if i & 2 else [])
            else:
                skcipher_op(sk, os.urandom(16), b"y" * 64, True, [16, 48] if i & 2 else [64])
        except OSError as e:
            errs[e.errno] = errs.get(e.errno, 0) + 1
    check(not errs, "errors on fresh request sockets: %s" % errs)

    errs = {}
    op, _ = sk.accept()
    for i in range(sockets):
        try:
            op.sendmsg_afalg([b"z" * 32], op=socket.ALG_OP_ENCRYPT, iv=bytes(16))
            if len(op.recv(32)) != 32:
                errs["short read"] = errs.get("short read", 0) + 1
        except OSError as e:
            errs[e.errno] = errs.get(e.errno, 0) + 1
    op.close()
    check(not errs, "errors when reusing a request socket: %s" % errs)

    res = {"ok": 0, "busy": 0, "again": 0}
    other = {}  # type: Dict[Any, int]
    lock = threading.Lock()
    ctr = tfm("skcipher", "ctr(aes)", key)
    for _ in range(50):
        op, _ = ctr.accept()
        op.sendmsg_afalg([], op=socket.ALG_OP_ENCRYPT, iv=bytes(16), flags=socket.MSG_MORE)

        def writer(op: socket.socket = op) -> None:
            for _ in range(20):
                try:
                    op.sendmsg([b"q" * 4096], [], socket.MSG_MORE | socket.MSG_DONTWAIT)
                    key = "ok"
                except OSError as e:
                    key = {errno.EBUSY: "busy", errno.EAGAIN: "again"}.get(e.errno or 0, "")
                    if not key:
                        with lock:
                            other[e.errno] = other.get(e.errno, 0) + 1
                        continue
                with lock:
                    res[key] += 1

        threads = [threading.Thread(target=writer) for _ in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        try:
            op.sendmsg([b"e" * 16], [], socket.MSG_DONTWAIT)
            op.setblocking(False)
            while op.recv(65536):
                pass
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EBUSY):
                other[e.errno] = other.get(e.errno, 0) + 1
        op.close()
    INFO.append("concurrent writers: %s" % res)
    check(res["ok"] > 0, "concurrent writers: no write ever succeeded")
    check(not other, "concurrent writers: unexpected errors %s" % other)
    for s in (aead, sk, ctr):
        s.close()

# ---------------------------------------------------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------------------------------------------------

def family(host: str) -> int:
    return socket.AF_INET6 if ":" in host else socket.AF_INET

def cmd_stream_recv(bind: str, port: int, timeout: int) -> None:
    """Accept one TCP connection, print the sha256 and length of everything received."""
    s = socket.socket(family(bind), socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((bind, port))
    s.listen(1)
    s.settimeout(timeout)
    c, _ = s.accept()
    c.settimeout(timeout)
    h, n = hashlib.sha256(), 0
    while True:
        d = c.recv(1 << 20)
        if not d:
            break
        h.update(d)
        n += len(d)
    INFO.append({"sha256": h.hexdigest(), "size": n})

def cmd_stream_send(dst: str, port: int, size: int) -> None:
    """Send `size` random bytes in irregular writes, print their sha256."""
    deadline = time.time() + 10
    while True:
        try:
            s = socket.create_connection((dst, port), timeout=30)
            break
        except ConnectionRefusedError:
            if time.time() > deadline:
                raise
            time.sleep(0.2)
    h, sent = hashlib.sha256(), 0
    block = os.urandom(1 << 20)
    while sent < size:
        n = min(size - sent, RND.randint(1, 70000))
        d = block[sent % len(block):][:n]
        s.sendall(d)
        h.update(d)
        sent += len(d)
    s.close()
    INFO.append({"sha256": h.hexdigest(), "size": sent})

def churn_client(host: str, port: int, deadline: float, idx: int, stats: Dict[str, Any], lock: threading.Lock) -> None:
    """Short connections: plain, TCP Fast Open, client RST, server RST and half-close, in turn."""
    i = 0
    while time.time() < deadline:
        i += 1
        kind = ("tfo", "plain", "rst", "rstsrv", "halfclose")[(i + idx) % 5]
        msg = (b"R" if kind == "rstsrv" else b"x") + os.urandom(15)
        try:
            c = socket.socket(family(host), socket.SOCK_STREAM)
            c.settimeout(5)
            if kind == "tfo":
                c.sendto(msg, MSG_FASTOPEN, (host, port))
            else:
                c.connect((host, port))
                c.sendall(msg)
            if kind == "halfclose":
                c.shutdown(socket.SHUT_WR)
            r = c.recv(64)
            if kind == "rst":
                c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            c.close()
            with lock:
                stats["ok" if r == msg[::-1] else "bad"] += 1
        except OSError as e:
            with lock:
                k = "%s:%s" % (kind, errno.errorcode.get(e.errno or 0, type(e).__name__))
                stats["errors"][k] = stats["errors"].get(k, 0) + 1

def churn_server(host: str, port: int, deadline: float) -> None:
    s = socket.socket(family(host), socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.IPPROTO_TCP, TCP_FASTOPEN, 4096)
    s.bind((host, port))
    s.listen(64)  # small backlog: overflows, and SYN cookies when enabled
    s.settimeout(1)

    def handle(c: socket.socket) -> None:
        try:
            c.settimeout(5)
            d = c.recv(64)
            if d:
                c.sendall(d[::-1])
            if d[:1] == b"R":
                c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        except OSError:
            pass
        finally:
            c.close()

    while time.time() < deadline:
        try:
            c, _ = s.accept()
        except OSError:
            continue
        threading.Thread(target=handle, args=(c,), daemon=True).start()
    s.close()

def cmd_tcp_server(host: str, port: int, secs: float) -> None:
    churn_server(host, port, time.time() + secs)
    INFO.append("server done")

def cmd_tcp_clients(host: str, port: int, secs: float, nclients: int) -> None:
    stats = {"ok": 0, "bad": 0, "errors": {}}  # type: Dict[str, Any]
    lock = threading.Lock()
    deadline = time.time() + secs
    clients = [threading.Thread(target=churn_client, args=(host, port, deadline, i, stats, lock))
               for i in range(nclients)]
    for c in clients:
        c.start()
    for c in clients:
        c.join()
    INFO.append(stats)
    check(stats["bad"] == 0, "%d connections got a corrupted reply" % stats["bad"])

def cmd_tcp_churn(host: str, port: int, secs: float, nclients: int) -> None:
    """Server and clients in the same process (loopback)."""
    server = threading.Thread(target=churn_server, args=(host, port, time.time() + secs + 3), daemon=True)
    server.start()
    time.sleep(0.5)
    cmd_tcp_clients(host, port, secs, nclients)

# ---------------------------------------------------------------------------------------------------------------------

COMMANDS = {
    "afalg-kat": (cmd_afalg_kat, ()),
    "aead-empty": (cmd_aead_empty, (str,)),
    "afalg-multipart": (cmd_afalg_multipart, (int,)),
    "afalg-splice": (cmd_afalg_splice, (str, int)),
    "afalg-concurrency": (cmd_afalg_concurrency, (int,)),
    "stream-recv": (cmd_stream_recv, (str, int, int)),
    "stream-send": (cmd_stream_send, (str, int, int)),
    "tcp-server": (cmd_tcp_server, (str, int, float)),
    "tcp-clients": (cmd_tcp_clients, (str, int, float, int)),
    "tcp-churn": (cmd_tcp_churn, (str, int, float, int)),
}  # type: Dict[str, Tuple[Any, Tuple[Any, ...]]]

def main() -> None:
    func, types = COMMANDS[sys.argv[1]]
    args = [ty(a) for ty, a in zip(types, sys.argv[2:])]
    try:
        func(*args)
    except Exception as e:
        FAILURES.append("%s raised %r" % (sys.argv[1], e))
    print(json.dumps({"failures": FAILURES, "info": INFO}))

if __name__ == "__main__":
    main()
