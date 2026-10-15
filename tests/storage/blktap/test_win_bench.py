from __future__ import annotations

import pytest

import hashlib
import json
import logging
import shlex
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from lib import config
from lib.blktap import VBDConnector
from lib.common import MiB, url_download, wait_for
from tests.storage.blktap import assert_tapdisk_io

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lib.host import Host
    from lib.vm import VM

# DiskSpd benchmark of a qcow2 or VHD VBD served by tapdisk, in a Windows guest: the Windows counterpart of
# test_fio_bench. DiskSpd (Microsoft, the engine of CrystalDiskMark) is a single executable: the test runner
# downloads a pinned release and copies it into the VM, which needs no internet access. Its threads (-t) and
# requests in flight per thread (-o) spread the I/O over the vCPUs, so over the queues xenvbd negotiated. The
# runs use the raw disk (no filesystem), with the caches disabled.
#
# Each run leaves the DiskSpd XML result and the tapdisk stats in <bench-outdir>/<format>/<run>/, and
# context.json (the VBD features offered by tapback and chosen by xenvbd) in <bench-outdir>/<format>/.
# The runs last --blktap bench-runtime seconds each. There is no performance threshold: the test only fails
# when DiskSpd fails, or when the I/O didn't go through tapdisk.
#
# Requirements:
# - an XCP-ng host with blktap and tapback, with 4 GiB free in --blktap bench-image-dir (one image at a time)
# - a Windows VM (cloned with 4 vCPUs by this test) with:
#   - Xen PV drivers (XCP-ng or XenServer VM tools) installed and working: the VBD is a PV disk
#   - user "root" with admin privileges, OpenSSH with Git Bash as its default shell (see
#     tests/guest_tools/win/test_nondestructive.py)

VCPU_COUNT = 4
DISKSPD_URL = 'https://github.com/microsoft/diskspd/releases/download/v2.2/DiskSpd.ZIP'
DISKSPD_SHA256 = '496df11e6375c1d564af3f8f2990734d9afc2b558469fd57b1bffa9313a5a6ce'
# the temporary directory of the root user, and the same as an SFTP path
GUEST_TEMP = 'C:/Users/root/AppData/Local/Temp'
GUEST_SFTP_TEMP = '/Users/root/AppData/Local/Temp'
GUEST_DIR = f'{GUEST_TEMP}/blktap-bench'
DISKSPD = f'{GUEST_DIR}/amd64/diskspd.exe'
WARMUP_SECS = 5

@dataclass
class DiskspdRun:
    name: str
    block_size: str
    outstanding: int  # requests in flight per thread
    threads: int
    random: bool
    write_percent: int

    def arguments(self, runtime: int, disk_number: int) -> list[str]:
        return [
            f'-b{self.block_size}', f'-o{self.outstanding}', f'-t{self.threads}',
            # sequential runs share the offset between the threads (-si), random ones are aligned on the block size
            '-r' if self.random else '-si', f'-w{self.write_percent}',
            f'-d{runtime}', f'-W{WARMUP_SECS}',
            # no software cache nor hardware write cache, latency statistics, XML output
            '-Sh', '-L', '-Rxml',
            f'#{disk_number}',
        ]

# the profiles of CrystalDiskMark (SEQ1M Q8T1, SEQ128K Q32T1, RND4K Q32T16, RND4K Q1T1), with one thread per
# vCPU instead of 16, and an OLTP-like mix of 70 % reads
RUNS = [
    DiskspdRun('seq1m-q8t1-read', '1M', 8, 1, False, 0),
    DiskspdRun('seq1m-q8t1-write', '1M', 8, 1, False, 100),
    DiskspdRun('seq128k-q32t1-read', '128K', 32, 1, False, 0),
    DiskspdRun('seq128k-q32t1-write', '128K', 32, 1, False, 100),
    DiskspdRun(f'rnd4k-q32t{VCPU_COUNT}-read', '4K', 32, VCPU_COUNT, True, 0),
    DiskspdRun(f'rnd4k-q32t{VCPU_COUNT}-write', '4K', 32, VCPU_COUNT, True, 100),
    DiskspdRun('rnd4k-q1t1-read', '4K', 1, 1, True, 0),
    DiskspdRun('rnd4k-q1t1-write', '4K', 1, 1, True, 100),
    DiskspdRun(f'rnd8k-q16t{VCPU_COUNT}-mix70', '8K', 16, VCPU_COUNT, True, 30),
]

def guest_disks(vm: VM) -> list[dict[str, Any]]:
    output = vm.execute_powershell_script(
        'ConvertTo-Json -Compress @(Get-Disk | Select-Object Number, Size, PartitionStyle, IsOffline, IsReadOnly)')
    return json.loads(output)

def wait_for_guest_disk(vm: VM, size_mib: int) -> int:
    """Number of the empty disk of the given size, once Windows sees it."""
    numbers = wait_for(lambda: [disk['Number'] for disk in guest_disks(vm)
                                if disk['Size'] == size_mib * MiB and disk['PartitionStyle'] == 'RAW'],
                       "Wait for the VBD to appear in Windows", timeout_secs=60)
    number = numbers[0]
    # a new disk may be offline, or read-only, depending on the SAN policy of the edition
    vm.execute_powershell_script(f'Set-Disk -Number {number} -IsOffline $false; '
                                 f'Set-Disk -Number {number} -IsReadOnly $false')
    return number

def prefill(vm: VM, disk_number: int, size_mib: int) -> None:
    """Write the whole disk once, so that the runs neither read unallocated blocks nor allocate them."""
    vm.execute_powershell_script(f"""$ErrorActionPreference = 'Stop'
$disk = [IO.FileStream]::new('\\\\.\\PhysicalDrive{disk_number}', 'Open', 'Write', 'ReadWrite')
$buffer = [byte[]]::new(1MB)
[Random]::new(1).NextBytes($buffer)
for ($i = 0; $i -lt {size_mib}; $i++) {{ $disk.Write($buffer, 0, $buffer.Length) }}
$disk.Flush()
$disk.Close()""")

def vbd_features(vm: VM, vbd_connector: VBDConnector, device: str) -> dict[str, dict[str, str | None]]:
    """What tapback offered for the VBD (backend), and what xenvbd chose (frontend), from XenStore."""
    backend = vbd_connector.backend_path(vm, device)
    frontend = vbd_connector.frontend_path(device)
    backend_keys = ['multi-queue-max-queues', 'max-ring-page-order', 'feature-max-indirect-segments',
                    'feature-persistent', 'feature-flush-cache', 'feature-barrier']
    frontend_keys = ['multi-queue-num-queues', 'ring-page-order', 'feature-persistent', 'protocol']
    return {
        'backend': {key: vbd_connector.xenstore.read(f'{backend}/{key}') for key in backend_keys},
        'frontend': {key: vm.xenstore_read(f'{frontend}/{key}', accept_unknown_key=True) for key in frontend_keys},
    }

def summary(result: ET.Element) -> str:
    """Bandwidth, IOPS and mean latency of each direction of a run, from the DiskSpd XML result."""
    secs = float(result.findtext('TimeSpan/TestTimeSeconds', '0'))
    assert secs > 0, \
        "no test time in the DiskSpd result"
    parts = []
    for direction in ('Read', 'Write'):
        total_bytes = sum(int(target.findtext(f'{direction}Bytes', '0'))
                          for target in result.iterfind('TimeSpan/Thread/Target'))
        count = sum(int(target.findtext(f'{direction}Count', '0'))
                    for target in result.iterfind('TimeSpan/Thread/Target'))
        if count:
            latency = result.findtext(f'TimeSpan/Latency/Average{direction}Milliseconds')
            parts.append(f"{direction.lower()} {total_bytes / secs / MiB:.1f} MiB/s {count / secs:.0f} IOPS"
                         + (f" lat {float(latency) * 1000:.0f} us" if latency else ""))
    return ', '.join(parts)

@pytest.fixture(scope='session')
def diskspd_zip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The DiskSpd release, downloaded once by the test runner and checked."""
    path = tmp_path_factory.mktemp('diskspd') / 'DiskSpd.zip'
    url_download(DISKSPD_URL, str(path))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == DISKSPD_SHA256, \
        f"{DISKSPD_URL} has SHA-256 {digest}, expected {DISKSPD_SHA256}"
    return path

@pytest.fixture
def vm_with_diskspd(vm_with_vcpu_count: VM, diskspd_zip: Path) -> VM:
    """The (halted) VM of vm_with_vcpu_count, started, with DiskSpd in GUEST_DIR."""
    vm = vm_with_vcpu_count
    vm.start()
    vm.wait_for_vm_running_and_ssh_up()
    assert vm.are_windows_tools_working(), \
        "the Windows VM must have working PV drivers: the VBD of the test is a PV disk"
    vm.sftp_put(str(diskspd_zip), f'{GUEST_SFTP_TEMP}/DiskSpd.zip')
    vm.execute_powershell_script(
        f"Expand-Archive -Force -Path '{GUEST_TEMP}/DiskSpd.zip' -DestinationPath '{GUEST_DIR}'")
    return vm
    # no teardown: vm_with_vcpu_count destroys the VM

@pytest.mark.small_vm
@pytest.mark.usefixtures("windows_vm")
@pytest.mark.parametrize('vm_with_vcpu_count', [VCPU_COUNT], indirect=True)
@pytest.mark.parametrize('bench_image', ['qcow2', 'vhd'], indirect=True)
def test_win_bench(host: Host, vm_with_diskspd: VM, bench_image: str, vbd_connector: VBDConnector,
                   bench_outdir: Path) -> None:
    """DiskSpd runs on a VBD served by tapdisk, in a Windows guest, with their results kept."""
    vm = vm_with_diskspd
    runtime = config.blktap.bench_runtime
    size_mib = config.blktap.bench_image_size or 4096
    existing = [run.name for run in RUNS if (bench_outdir / run.name).exists()]
    assert not existing, \
        f"results already in {bench_outdir} for {', '.join(existing)}: choose another --blktap bench-outdir"

    device = 'xvdb'
    pid, minor = vbd_connector.connect(vm, bench_image, device)
    disk_number = wait_for_guest_disk(vm, size_mib)
    features = vbd_features(vm, vbd_connector, device)
    (bench_outdir / 'context.json').write_text(json.dumps(features, indent=1))
    logging.info(f"Windows disk {disk_number}, tapback offered {features['backend']}, "
                 f"xenvbd chose {features['frontend']}")
    prefill(vm, disk_number, size_mib)
    duration_min = len(RUNS) * (runtime + WARMUP_SECS) / 60
    logging.info(f"{len(RUNS)} DiskSpd runs of {runtime} s, about {duration_min:.0f} min: results in {bench_outdir}")

    failures = []
    for index, run in enumerate(RUNS, start=1):
        logging.info(f"DiskSpd run {index}/{len(RUNS)}: {run.name}")
        result = vm.ssh_with_result(shlex.join([DISKSPD] + run.arguments(runtime, disk_number)))
        rundir = bench_outdir / run.name
        rundir.mkdir()
        (rundir / 'diskspd.xml').write_text(result.stdout)
        stats = vbd_connector.tapctl.stats(pid, minor)
        (rundir / 'tapdisk-stats.json').write_text(json.dumps(stats, indent=1))
        # stderr is merged into the output: the XML document starts at <Results>
        start = result.stdout.find('<Results')
        if result.returncode != 0 or start == -1:
            failures.append(f"{run.name}: DiskSpd exited with {result.returncode}: {result.stdout.strip()[:500]}")
            continue
        logging.info(f"{run.name}: {summary(ET.fromstring(result.stdout[start:]))}")

    assert not failures, \
        "DiskSpd runs failed:\n" + "\n".join(failures)
    # the guest I/O went through the tapdisk of the VBD
    assert_tapdisk_io(host, pid, minor, size_mib=1)
