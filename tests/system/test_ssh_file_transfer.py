import pytest

import hashlib
import os
import tempfile
from pathlib import Path

from lib.commands import local_cmd, sftp
from lib.common import Defer, InterruptShield
from lib.host import Host

from typing import Literal, TypeAlias, get_args

# Requirements:
# - an XCP-ng host (--hosts) >= 8.3

REMOTE_DIR = "/tmp"

Protocol: TypeAlias = Literal['sftp', 'legacy']

def _sha256(path: Path) -> str:
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()

def _make_random_local_file(interrupt_shield: InterruptShield, size: int = 1_000_000) -> Path:
    with interrupt_shield() as defer, \
            tempfile.NamedTemporaryFile(prefix="xcpng-tests-ssh-transfer-", delete=False) as f:
        defer(lambda: os.remove(f.name))
        f.write(os.urandom(size))
    return Path(f.name)

def _scp(src: str, dest: str, protocol: Protocol) -> None:
    opts = ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null', '-o', 'LogLevel=ERROR']
    if protocol == 'legacy':
        opts.append('-O')
    local_cmd(['scp'] + opts + [src, dest])

@pytest.mark.parametrize("protocol", get_args(Protocol))
def test_scp_roundtrip(host: Host, defer: Defer, interrupt_shield: InterruptShield, protocol: Protocol) -> None:
    local_src = _make_random_local_file(interrupt_shield)
    remote_path = f"{REMOTE_DIR}/{local_src.name}"
    defer(lambda: host.ssh(f"rm -f {remote_path}"))

    _scp(str(local_src), f"root@{host.hostname_or_ip}:{remote_path}", protocol)
    remote_sha256 = host.ssh(f"sha256sum {remote_path}").split()[0]
    assert remote_sha256 == _sha256(local_src), "uploaded file content differs from the original"

    local_dst = local_src.with_name(local_src.name + ".download")
    defer(lambda: local_dst.unlink(missing_ok=True))
    _scp(f"root@{host.hostname_or_ip}:{remote_path}", str(local_dst), protocol)
    assert _sha256(local_dst) == _sha256(local_src), "downloaded file content differs from the original"

def test_sftp_batch_roundtrip(host: Host, defer: Defer, interrupt_shield: InterruptShield) -> None:
    local_src = _make_random_local_file(interrupt_shield)
    remote_path = f"{REMOTE_DIR}/{local_src.name}"
    defer(lambda: host.ssh(f"rm -f {remote_path}"))

    put_res = sftp(host.hostname_or_ip, [f"put {local_src} {remote_path}", "bye"])
    assert put_res.returncode == 0, put_res.stdout.decode()
    remote_sha256 = host.ssh(f"sha256sum {remote_path}").split()[0]
    assert remote_sha256 == _sha256(local_src), "uploaded file content differs from the original"

    local_dst = local_src.with_name(local_src.name + ".download")
    defer(lambda: local_dst.unlink(missing_ok=True))
    get_res = sftp(host.hostname_or_ip, [f"get {remote_path} {local_dst}", "bye"])
    assert get_res.returncode == 0, get_res.stdout.decode()
    assert _sha256(local_dst) == _sha256(local_src), "downloaded file content differs from the original"
