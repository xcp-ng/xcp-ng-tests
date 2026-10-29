from __future__ import annotations

import pytest

import json
import logging
import re
import shlex
import time
from dataclasses import dataclass
from pathlib import Path

from lib.blktap import VBDConnector
from tests.storage.blktap import assert_tapdisk_io, prefill, wait_for_guest_device
from tests.storage.blktap.conftest import _bench_image, _bench_outdir
from tests.storage.blktap.test_app_bench import (
    DEVICE,
    MEMORY,
    MOUNTPOINT,
    VCPU_COUNT,
    cpu_snapshot,
    cpu_usage,
    guest_run,
    make_filesystem,
    unmount,
)

from typing import TYPE_CHECKING, Callable, Generator

if TYPE_CHECKING:
    from lib.common import Defer
    from lib.host import Host
    from lib.vm import VM

# Phoronix Test Suite (PTS) profiles as application workloads of a qcow2 or VHD VBD served by tapdisk, so that the
# results can be compared with those of bench.vates.tech: the same profiles, as the PTS client runs them. Like
# test_app_bench, the VBD is written whole once, each run uses a new ext4 filesystem of the VBD, and leaves its
# output, its metrics (result.json), the CPU time of the tapdisk threads and of dom0 (cpu.json) and the tapdisk
# stats in <bench-outdir>/<format>/<run>/. There is no performance threshold: the test only fails when a
# run fails, or when the I/O didn't go through tapdisk.
#
# - pts/fs-mark (FS-Mark 3.3, files/s): creates files of 1 MB and syncs them. It keeps the files of all its loops: the
#   profile writes 20 to 40 GB, so the VBD is PTS_IMAGE_MIB, not the 10 GiB of test_app_bench. PTS reports the
#   result of the first loop only; result.json also has the mean of all the loops.
# - pts/dbench (dbench 4.0, MB/s): replays a file server trace with a number of clients. The profile has no time
#   limit, so dbench runs 10 minutes per number of clients: DBENCH_SECONDS instead.
#
# Requirements:
# - an XCP-ng host with blktap and tapback, with PTS_IMAGE_MIB free in --blktap bench-image-dir (one image at a time)
# - a Debian 12 VM with internet access, to install PTS and download the test profiles (cloned with VCPU_COUNT
#   vCPUs and MEMORY of RAM by this test)

PTS_VERSION = '10.8.4'
PTS_DEB = f'phoronix-test-suite_{PTS_VERSION}_all.deb'
PTS_DEB_URL = f'https://github.com/phoronix-test-suite/phoronix-test-suite/releases/download/v{PTS_VERSION}/{PTS_DEB}'
PTS_DEB_SHA256 = 'be81f71fc0382a7725dc88f4a18f013d1c3f6939d440629231d392a11816feca'
PTS = 'phoronix-test-suite'
# batch mode without prompt nor result upload, and a fixed number of runs (the passes of the benchmark repeat them)
PTS_BATCH_SETTINGS = {
    'BatchMode/Configured': 'TRUE', 'BatchMode/SaveResults': 'TRUE', 'BatchMode/UploadResults': 'FALSE',
    'BatchMode/OpenBrowser': 'FALSE', 'BatchMode/PromptForTestIdentifier': 'FALSE',
    'BatchMode/PromptForTestDescription': 'FALSE', 'BatchMode/PromptSaveName': 'FALSE',
    'BatchMode/RunAllTestCombinations': 'FALSE', 'TestResultValidation/DynamicRunCount': 'FALSE',
}
# as root, PTS installs the tests and keeps its results here
PTS_DIR = '/var/lib/phoronix-test-suite'
FS_MARK = 'pts/fs-mark-1.0.3'
DBENCH = 'pts/dbench-1.0.2'
# the run script of each profile
PTS_PROFILES = {
    FS_MARK: f'{PTS_DIR}/installed-tests/{FS_MARK}/fs-mark',
    DBENCH: f'{PTS_DIR}/installed-tests/{DBENCH}/dbench',
}
PTS_IMAGE_MIB = 51200
DBENCH_SECONDS = 60

@dataclass(frozen=True)
class PtsRun:
    name: str
    profile: str  # pts/<test>-<version>
    option: str  # identifier of the option of the profile
    arguments: str  # its value

    @property
    def test(self) -> str:
        return self.profile.split('/')[1].rsplit('-', 1)[0]

# The values of the "test" menu of pts/fs-mark-1.0.3 (PTS splits the name of an entry at its commas, so the values
# are given rather than the names): 1000 Files, 1MB Size; 4000 Files, 32 Sub Dirs, 1MB Size. Two entries are left out,
# because they write 100 GB: "1000 Files, 1MB Size, No Sync/FSync" (-L 100 -s 1048576 -n 1000 -S 0), and "5000 Files,
# 1MB Size, 4 Threads" (-L 5 -s 1048576 -n 5000 -t 4, where -n is the number of files of each thread).
FS_MARK_RUNS = [
    PtsRun('fs-mark-1000files', FS_MARK, 'test', '-L 20 -s 1048576 -n 1000'),
    PtsRun('fs-mark-4000files-32dirs', FS_MARK, 'test', '-L 10 -s 1048576 -n 4000 -D 32'),
]
# The "client-count" menu of pts/dbench-1.0.2 is the number of clients: its value is also given the time limit and
# the working directory, on the VBD
DBENCH_RUNS = [
    PtsRun(f'dbench-{clients}clients', DBENCH, 'client-count',
           f'{clients} -t {DBENCH_SECONDS} -D {MOUNTPOINT}/dbench')
    for clients in (1, 6, 12, 48, 128, 256)
]

def install_pts(vm: VM) -> None:
    """PTS (not in Debian 12) in batch mode, and the profiles. What is already there is kept."""
    guest_run(vm, 'DEBIAN_FRONTEND=noninteractive apt-get install -y -q build-essential curl ca-certificates fio')
    guest_run(vm, f'command -v {PTS} || (cd /root && curl -sSfLO {PTS_DEB_URL} '
                  f'&& echo "{PTS_DEB_SHA256}  {PTS_DEB}" | sha256sum -c - '
                  f'&& DEBIAN_FRONTEND=noninteractive apt-get install -y -q ./{PTS_DEB})')
    settings = [f'PhoronixTestSuite/Options/{key}={value}' for key, value in PTS_BATCH_SETTINGS.items()]
    guest_run(vm, f'{PTS} enterprise-setup && {PTS} user-config-set {" ".join(settings)}')
    for profile, script in PTS_PROFILES.items():
        guest_run(vm, f'{PTS} batch-install {profile}')
        installed = vm.ssh_with_result(f'test -x {script}')
        assert installed.returncode == 0, \
            f"{profile} is not installed: no {script}"

def pts_benchmark(vm: VM, run: PtsRun) -> tuple[str, str]:
    """Run a profile with the value of one of its options: the PTS output, and the log of the test."""
    results = f'blktap-{run.name}-{int(time.time())}'
    environment = {'PTS_SILENT_MODE': '1', 'TEST_RESULTS_NAME': results, 'TEST_RESULTS_IDENTIFIER': results,
                   'TEST_RESULTS_DESCRIPTION': results, 'FORCE_TIMES_TO_RUN': '1',
                   'PRESET_OPTIONS_VALUES': f'{run.test}.{run.option}={run.arguments}'}
    assignments = ' '.join(f'{name}={shlex.quote(value)}' for name, value in environment.items())
    output = guest_run(vm, f'{assignments} {PTS} batch-benchmark {run.profile} 2>&1')
    log = guest_run(vm, f'cat {PTS_DIR}/test-results/{results}/test-logs/*/{results}.log')
    return output, log

def run_fs_mark(vm: VM, outdir: Path, run: PtsRun) -> dict[str, float]:
    """pts/fs-mark with the arguments of one entry of its menu, the files written on the VBD."""
    scratch = f'{PTS_PROFILES[FS_MARK].rsplit("/", 1)[0]}/fs_mark-3.3/scratch'
    # the run script of the profile writes in its scratch directory, which is bind mounted on the VBD (and which
    # it empties at the end)
    guest_run(vm, f'mkdir -p {MOUNTPOINT}/fs-mark {scratch} && mount --bind {MOUNTPOINT}/fs-mark {scratch}')
    try:
        output, log = pts_benchmark(vm, run)
    finally:
        guest_run(vm, f'umount {scratch}')
    (outdir / 'output.txt').write_text(output + '\n' + log)
    average = re.search(r'Average: ([\d.]+) Files/s', output)
    assert average and 'non-zero exit status' not in output, \
        f"no result in the PTS output, see {outdir / 'output.txt'}"
    # the rows of fs_mark: FSUse%, Count, Size, Files/sec, App Overhead
    rows = re.findall(r'^\s*\d+\s+\d+\s+\d+\s+([\d.]+)\s+\d+\s*$', log, re.MULTILINE)
    loops = [float(files_per_sec) for files_per_sec in rows]
    assert loops, \
        f"no loop in the fs_mark log, see {outdir / 'output.txt'}"
    return {'files_per_sec': float(average.group(1)), 'files_per_sec_loops_mean': sum(loops) / len(loops),
            'loops': float(len(loops))}

def run_dbench(vm: VM, outdir: Path, run: PtsRun) -> dict[str, float]:
    """pts/dbench with a number of clients, its working directory on the VBD."""
    guest_run(vm, f'mkdir -p {MOUNTPOINT}/dbench')
    output, log = pts_benchmark(vm, run)
    (outdir / 'output.txt').write_text(output + '\n' + log)
    average = re.search(r'Average: ([\d.]+) MB/s', output)
    latency = re.search(r'max_latency=([\d.]+) ms', log)
    assert average and latency and 'non-zero exit status' not in output, \
        f"no result in the PTS output, see {outdir / 'output.txt'}"
    return {'mb_per_sec': float(average.group(1)), 'max_latency_ms': float(latency.group(1))}

RUNNERS: dict[str, Callable[[VM, Path, PtsRun], dict[str, float]]] = {
    FS_MARK: run_fs_mark,
    DBENCH: run_dbench,
}
PTS_RUNS = FS_MARK_RUNS + DBENCH_RUNS

@pytest.fixture
def pts_bench_image(request: pytest.FixtureRequest, host: Host) -> Generator[str, None, None]:
    """Image of the PTS benchmark, format by indirect param."""
    yield from _bench_image(host, request.param, PTS_IMAGE_MIB)

@pytest.fixture
def pts_bench_outdir(pts_bench_image: str) -> Path:
    """Results directory of the benchmarks of pts_bench_image."""
    return _bench_outdir(pts_bench_image)

@pytest.fixture
def vm_with_pts(vm_with_vcpu_count: VM) -> VM:
    """The (halted) VM of vm_with_vcpu_count, with MEMORY of RAM, started, with PTS and its profiles."""
    vm = vm_with_vcpu_count
    vm.host.xe('vm-memory-limits-set', {'uuid': vm.uuid, 'static-min': MEMORY, 'static-max': MEMORY,
                                        'dynamic-min': MEMORY, 'dynamic-max': MEMORY})
    vm.start()
    vm.wait_for_vm_running_and_ssh_up()
    install_pts(vm)
    return vm
    # no teardown: vm_with_vcpu_count destroys the VM

@pytest.mark.big_vm
@pytest.mark.unix_vm
@pytest.mark.parametrize('vm_with_vcpu_count', [VCPU_COUNT], indirect=True)
@pytest.mark.parametrize('pts_bench_image', ['qcow2', 'vhd'], indirect=True)
def test_pts_bench(host: Host, vm_with_pts: VM, pts_bench_image: str, vbd_connector: VBDConnector,
                   pts_bench_outdir: Path, defer: Defer) -> None:
    """PTS profiles on a filesystem of a VBD served by tapdisk, with their results kept."""
    vm = vm_with_pts
    existing = [run.name for run in PTS_RUNS if (pts_bench_outdir / run.name).exists()]
    assert not existing, \
        f"results already in {pts_bench_outdir} for {', '.join(existing)}: choose another --blktap bench-outdir"

    pid, minor = vbd_connector.connect(vm, pts_bench_image, DEVICE)
    wait_for_guest_device(vm, DEVICE)
    defer(lambda: vm.ssh(f'umount {MOUNTPOINT}', check=False))
    prefill(vm, DEVICE)

    failures = []
    for index, run in enumerate(PTS_RUNS, start=1):
        logging.info(f"PTS run {index}/{len(PTS_RUNS)}: {run.name}")
        outdir = pts_bench_outdir / run.name
        outdir.mkdir()
        make_filesystem(vm)
        cpu_before = cpu_snapshot(host, pid)
        start = time.monotonic()
        try:
            metrics = RUNNERS[run.profile](vm, outdir, run)
        except Exception as e:
            failures.append(f"{run.name}: {e}")
            continue
        finally:
            unmount(vm)
        cpu = cpu_usage(cpu_before, cpu_snapshot(host, pid), time.monotonic() - start)
        (outdir / 'cpu.json').write_text(json.dumps(cpu, indent=1))
        (outdir / 'result.json').write_text(json.dumps(metrics, indent=1))
        (outdir / 'tapdisk-stats.json').write_text(json.dumps(vbd_connector.tapctl.stats(pid, minor), indent=1))
        logging.info(f"{run.name}: " + ', '.join(f'{name} {value:.1f}' for name, value in metrics.items())
                     + f", tapdisk CPU {cpu['tapdisk_cpu_pct']:.0f} %, dom0 busy {cpu['dom0_busy_pct']:.0f} %")

    assert not failures, \
        "PTS runs failed:\n" + "\n".join(failures)
    # the guest I/O went through the tapdisk of the VBD
    assert_tapdisk_io(host, pid, minor, size_mib=1)
