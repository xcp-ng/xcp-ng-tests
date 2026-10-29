from __future__ import annotations

import pytest

import shlex

from lib.common import MiB
from tests.storage.blktap import PATTERN_BLOCKS, blktap_tool, filled_md5, nbd_blocks, nbd_socket, stop_tapdisk
from tests.storage.blktap.test_nbd import nbd_client

from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from lib.blktap import TapCtl
    from lib.host import Host

# The vhd-util command, run directly on VHD files in a temporary directory of dom0 (the one of the blktap
# build in /opt/blktap if there is one, else the one of the PATH).
#
# Requirements:
# - an XCP-ng host with blktap

VIRTUAL_SIZE_MIB = 32
BLOCK_SIZE = 2 * MiB

class VhdUtil:
    """vhd-util in a working directory of dom0, removed at teardown."""

    def __init__(self, host: Host) -> None:
        self.host = host
        self.tool = blktap_tool(host, 'vhd-util')
        self.directory = host.ssh('mktemp -d /tmp/blktap-test-XXXXXX')

    def run(self, *args: object) -> tuple[int, str]:
        """Run vhd-util, return its exit code with its output (stderr included)."""
        command = shlex.join([self.tool] + [str(arg) for arg in args])
        # the dump of the headers of a corrupted file has binary garbage
        result = self.host.ssh_with_result(
            f"cd {self.directory} && {command} 2>&1 | tr -c '[:print:]\\n' '?'; exit ${{PIPESTATUS[0]}}")
        return result.returncode, result.stdout

    def ok(self, *args: object) -> str:
        code, output = self.run(*args)
        assert code == 0, f"vhd-util {' '.join(map(str, args))} failed with {code}: {output}"
        return output.strip()

    def path(self, name: str) -> str:
        return f'{self.directory}/{name}'

    def sh(self, command: str) -> str:
        return self.host.ssh(f'cd {self.directory} && {command}')

    def output_size(self, *args: object) -> int:
        """Size of what vhd-util prints, for the commands printing binary data."""
        command = shlex.join([self.tool] + [str(arg) for arg in args])
        return int(self.sh(f'{command} 2>&1 | wc -c'))

    def md5(self, name: str, offset: int, length: int) -> str:
        """MD5 of a range of the virtual disk, read by vhd-util."""
        # -r reads sectors
        command = shlex.join([self.tool, 'read', '-n', name, '-r', str(offset // 512), '-c', str(length // 512)])
        return self.sh(f'{command} | md5sum').split()[0]

@pytest.fixture
def vhd_util(host: Host) -> Generator[VhdUtil, None, None]:
    tool = VhdUtil(host)
    yield tool
    host.ssh(f'rm -rf {tool.directory}')

@pytest.fixture
def base_vhd(vhd_util: VhdUtil) -> str:
    vhd_util.ok('create', '-n', 'base.vhd', '-s', VIRTUAL_SIZE_MIB)
    return 'base.vhd'

@pytest.mark.no_vm
def test_create_and_query(vhd_util: VhdUtil, base_vhd: str) -> None:
    assert vhd_util.ok('query', '-n', base_vhd, '-v') == str(VIRTUAL_SIZE_MIB), "Unexpected virtual size"
    assert int(vhd_util.ok('query', '-n', base_vhd, '-s')) < MiB, "A new VHD is sparse"
    assert 'has no parent' in vhd_util.ok('query', '-n', base_vhd, '-p'), "A new VHD has a parent"
    assert vhd_util.ok('query', '-n', base_vhd, '-f') == 'hidden: 0', "A new VHD is hidden"
    assert vhd_util.ok('query', '-n', base_vhd, '-m') == 'marker: 0', "A new VHD has the marker set"
    assert vhd_util.ok('query', '-n', base_vhd, '-d') == 'chain depth: 1', "A new VHD is in a chain"
    assert vhd_util.ok('query', '-n', base_vhd, '-a') == '0', "A new VHD has allocated blocks"
    assert vhd_util.ok('check', '-n', base_vhd) == f'{base_vhd} is valid', "A new VHD isn't valid"

@pytest.mark.no_vm
def test_create_with_options(vhd_util: VhdUtil) -> None:
    """-S preallocates the metadata for a fast resize, -r reserves the space of the whole disk."""
    vhd_util.ok('create', '-n', 'prealloc.vhd', '-s', 16, '-S', 64)
    assert vhd_util.ok('query', '-n', 'prealloc.vhd', '-S') == '64', "Unexpected preallocated metadata size"
    vhd_util.ok('resize', '-n', 'prealloc.vhd', '-s', 48, '-f')
    assert vhd_util.ok('query', '-n', 'prealloc.vhd', '-v') == '48', "The fast resize didn't change the size"

    vhd_util.ok('create', '-n', 'reserved.vhd', '-s', 8, '-r')
    assert int(vhd_util.ok('query', '-n', 'reserved.vhd', '-s')) >= 8 * MiB, "-r didn't reserve the space"

@pytest.mark.no_vm
def test_fill_and_read(vhd_util: VhdUtil, base_vhd: str) -> None:
    for option in ('-t', '-b'):
        assert 'not allocated' in vhd_util.ok('read', '-n', base_vhd, option, 0), \
            f"read {option}: the first block of a new VHD is allocated"

    vhd_util.ok('fill', '-n', base_vhd)
    assert vhd_util.ok('query', '-n', base_vhd, '-a') == str(VIRTUAL_SIZE_MIB * MiB // BLOCK_SIZE), \
        "fill didn't allocate every block"
    assert int(vhd_util.ok('query', '-n', base_vhd, '-s')) >= VIRTUAL_SIZE_MIB * MiB, \
        "The filled VHD is smaller than its virtual size"

    assert 'conectix' in vhd_util.ok('read', '-n', base_vhd, '-p'), "read -p doesn't print the footer"
    for option in ('-t', '-b'):
        assert 'not allocated' not in vhd_util.ok('read', '-n', base_vhd, option, 0), \
            f"read {option}: the first block of a filled VHD isn't allocated"
    for option, argument in (('-B', None), ('-a', None), ('-m', 0), ('-j', 0), ('-e', 0)):
        arguments = [option] if argument is None else [option, argument]
        assert vhd_util.output_size('read', '-n', base_vhd, *arguments) > 0, f"read {option} prints nothing"
    assert vhd_util.md5(base_vhd, 0, 4096) == filled_md5(0, 4096), "A filled VHD isn't zeroed"

@pytest.mark.no_vm
def test_snapshot_chain(vhd_util: VhdUtil, base_vhd: str) -> None:
    vhd_util.ok('snapshot', '-n', 'child.vhd', '-p', base_vhd)
    assert vhd_util.ok('query', '-n', 'child.vhd', '-p') == vhd_util.path(base_vhd), "Unexpected parent"
    assert vhd_util.ok('query', '-n', 'child.vhd', '-d') == 'chain depth: 2', "Unexpected chain depth"

    vhd_util.ok('set', '-n', 'child.vhd', '-f', 'hidden', '-v', 1)
    vhd_util.ok('set', '-n', 'child.vhd', '-f', 'marker', '-v', 1)
    assert vhd_util.ok('query', '-n', 'child.vhd', '-f') == 'hidden: 1', "The hidden flag isn't set"
    assert vhd_util.ok('query', '-n', 'child.vhd', '-m') == 'marker: 1', "The marker isn't set"

    check = vhd_util.ok('check', '-n', 'child.vhd', '-p', '-b')
    assert check.count('is valid') == 2, f"Both VHDs of the chain should be valid: {check}"
    assert 'secs allocated' in vhd_util.ok('check', '-n', 'child.vhd', '-s'), "check -s prints no statistics"

    code, output = vhd_util.run('snapshot', '-n', 'too-deep.vhd', '-p', 'child.vhd', '-l', 1)
    assert code != 0 and 'depth exceeded' in output, f"The depth limit isn't enforced: {output}"

@pytest.mark.no_vm
def test_snapshot_of_a_raw_image(vhd_util: VhdUtil) -> None:
    vhd_util.sh('truncate -s 8M raw.img')
    vhd_util.ok('snapshot', '-n', 'on-raw.vhd', '-p', 'raw.img', '-m')
    assert vhd_util.ok('query', '-n', 'on-raw.vhd', '-v') == '8', "The snapshot doesn't have the size of the raw image"

@pytest.mark.no_vm
def test_scan(vhd_util: VhdUtil, base_vhd: str) -> None:
    vhd_util.ok('snapshot', '-n', 'child.vhd', '-p', base_vhd)
    vhd_util.ok('set', '-n', base_vhd, '-f', 'hidden', '-v', 1)

    output = vhd_util.ok('scan', '-f', '-p', '-a', '-m', '*.vhd', 'child.vhd', base_vhd)
    assert f'vhd={base_vhd} ' in output and 'hidden=1' in output, f"Unexpected scan output: {output}"
    assert 'vhd=child.vhd ' in output and f'parent={base_vhd}' in output, f"Unexpected scan output: {output}"
    # the parents are resolved by -a
    assert vhd_util.path(base_vhd) in vhd_util.ok('scan', '-v', '-a', 'child.vhd'), "scan -a doesn't resolve the parent"
    assert 'marker=' in vhd_util.ok('scan', '-M', '-f', '-p', base_vhd), "scan -M doesn't print the marker"

    output = vhd_util.ok('scan', '-c', '-f', base_vhd, 'nonexistent.vhd')
    assert 'scan-error' in output and f'vhd={base_vhd} ' in output, \
        f"-c should go on after a failure: {output}"

@pytest.mark.no_vm
def test_coalesce(vhd_util: VhdUtil, tapctl: TapCtl, base_vhd: str) -> None:
    """The blocks written to a child are merged in its parent, whose content then is the pattern."""
    vhd_util.ok('snapshot', '-n', 'child.vhd', '-p', base_vhd)
    pid, minor = tapctl.create(f"vhd:{vhd_util.path('child.vhd')}")
    nbd_client(vhd_util.host, nbd_socket(pid, minor), 'write', nbd_blocks(PATTERN_BLOCKS))
    stop_tapdisk(vhd_util.host, tapctl, pid, minor)
    for offset, length, fill in PATTERN_BLOCKS:
        assert vhd_util.md5(base_vhd, offset, length) != filled_md5(fill, length), \
            f"The parent has the data of the child before the coalesce (offset {offset})"

    assert 'Coalesced' in vhd_util.ok('coalesce', '-n', 'child.vhd', '-p'), "coalesce doesn't report its work"

    for offset, length, fill in PATTERN_BLOCKS:
        assert vhd_util.md5(base_vhd, offset, length) == filled_md5(fill, length), \
            f"The block at {offset} isn't in the parent after the coalesce"
    vhd_util.ok('check', '-n', base_vhd)

@pytest.mark.no_vm
def test_copy(vhd_util: VhdUtil, base_vhd: str) -> None:
    vhd_util.ok('fill', '-n', base_vhd)
    vhd_util.ok('copy', '-n', base_vhd, '-N', 'copy.vhd')
    vhd_util.ok('check', '-n', 'copy.vhd')
    size = VIRTUAL_SIZE_MIB * MiB
    assert vhd_util.md5('copy.vhd', 0, size) == vhd_util.md5(base_vhd, 0, size), "The copy differs from the original"

@pytest.mark.no_vm
def test_resize(vhd_util: VhdUtil, base_vhd: str) -> None:
    vhd_util.ok('resize', '-n', base_vhd, '-s', 64, '-j', 'grow.journal')
    assert vhd_util.ok('query', '-n', base_vhd, '-v') == '64', "The resize didn't change the size"
    vhd_util.ok('check', '-n', base_vhd)

    code, output = vhd_util.run('resize', '-n', base_vhd, '-s', 16, '-j', 'shrink.journal')
    assert code != 0 and 'shrink' in output, f"Shrinking a dynamic VHD should be refused: {output}"
    assert vhd_util.ok('query', '-n', base_vhd, '-v') == '64', "The refused shrink changed the size"

    code, output = vhd_util.run('revert', '-n', base_vhd, '-j', 'nonexistent.journal')
    assert code != 0 and 'journal' in output, f"Reverting without a journal should fail: {output}"

@pytest.mark.no_vm
def test_modify_and_set(vhd_util: VhdUtil, base_vhd: str) -> None:
    vhd_util.ok('create', '-n', 'other.vhd', '-s', VIRTUAL_SIZE_MIB)
    vhd_util.ok('snapshot', '-n', 'child.vhd', '-p', base_vhd)

    vhd_util.ok('modify', '-n', 'child.vhd', '-p', 'other.vhd')
    assert vhd_util.ok('query', '-n', 'child.vhd', '-p') == vhd_util.path('other.vhd'), \
        "modify -p didn't change the parent"

    code, output = vhd_util.run('set', '-n', base_vhd, '-f', 'bogus', '-v', 1)
    assert code != 0 and 'invalid field' in output, f"An unknown field should be refused: {output}"

@pytest.mark.no_vm
def test_key(vhd_util: VhdUtil, base_vhd: str) -> None:
    """The key hash is stored in the VHD; the key file must be named after the VHD and the key size."""
    key, wrong_key = 'base,aes-xts-plain,512.key', 'base,aes-xts-plain,256.key'
    vhd_util.sh(f"head -c 64 /dev/urandom > '{key}'; head -c 32 /dev/urandom > '{wrong_key}'")

    assert vhd_util.ok('key', '-p', '-n', base_vhd) == 'none', "A new VHD has a key hash"
    vhd_util.ok('key', '-s', '-n', base_vhd, '-k', key)
    # the hash printed by -C is the one stored in the VHD
    expected_hash = vhd_util.ok('key', '-C', '-k', key).splitlines()[-1]
    assert expected_hash in vhd_util.ok('key', '-p', '-n', base_vhd), "The hash stored isn't the one of the key"
    vhd_util.ok('key', '-c', '-n', base_vhd, '-k', key)

    code, output = vhd_util.run('key', '-c', '-n', base_vhd, '-k', wrong_key)
    assert code != 0 and "doesn't match" in output, f"A wrong key should be refused: {output}"
    code, output = vhd_util.run('key', '-s', '-n', 'base.vhd', '-k', 'wrong-name.key')
    assert code != 0 and 'Invalid key name' in output, f"A badly named key file should be refused: {output}"

@pytest.mark.no_vm
def test_invalid_files(vhd_util: VhdUtil, base_vhd: str) -> None:
    """Truncated or random files are reported (non-zero exit code and a message), never a crash."""
    vhd_util.sh(f'cp {base_vhd} truncated.vhd && truncate -s 2000 truncated.vhd')
    vhd_util.sh('head -c 100000 /dev/urandom > junk.vhd')

    for name in ('truncated.vhd', 'junk.vhd'):
        code, output = vhd_util.run('check', '-n', name)
        assert 0 < code < 128 and 'appears invalid' in output, f"check {name}: {code} {output[:300]}"

    code, output = vhd_util.run('query', '-n', 'junk.vhd', '-v')
    assert 0 < code < 128 and 'error opening' in output, f"query junk.vhd: {code} {output[:300]}"

    assert vhd_util.run('repair', '-n', base_vhd)[0] == 0, "Repairing a valid VHD should work"
    assert 0 < vhd_util.run('repair', '-b', '-n', 'truncated.vhd')[0] < 128, "Repairing a truncated VHD should fail"
