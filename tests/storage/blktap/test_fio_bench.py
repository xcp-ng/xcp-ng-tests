from __future__ import annotations

import pytest

import io
import itertools
import json
import logging
import shlex
import tarfile
from dataclasses import dataclass
from pathlib import Path

from lib import config
from lib.blktap import VBDConnector
from tests.storage.blktap import assert_tapdisk_io, prefill, wait_for_guest_device

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lib.common import Defer
    from lib.host import Host
    from lib.vm import VM

# fio benchmark of a qcow2 or VHD VBD served by tapdisk, from blktap-bench: setup-qcow2.sh or setup-vhd.sh
# (backend tapdisk, image on a filesystem) then fio-bench-timeseries-all.sh. Each fio run leaves its JSON
# result, its bandwidth/IOPS/latency logs, context information and the tapdisk stats in
# <--blktap-bench-outdir>/<format>/<rw>-bs<bs>-j<jobs>-d<iodepth>/, the layout read by the blktap-bench
# analysis tools (extract-fio-stats.py, viewer). There is no performance threshold: the test only fails
# when fio fails, or when the I/O didn't go through tapdisk.
#
# The runs are chosen with --blktap-bench-matrix, and last --blktap-bench-runtime seconds each. The
# tapdisk polling values are set with --blktap-polling-duration and --blktap-polling-idle-threshold.
#
# Requirements:
# - an XCP-ng host with blktap and tapback, with 4 GiB free in dom0 /tmp (one image at a time)
# - a small unix VM, with internet access to install fio (cloned with 4 vCPUs by this test)

DEVICE = "xvdb"
RAMP_TIME_SECS = 5
LOG_INTERVAL_MSECS = 1000
GUEST_WORKDIR = '/tmp/fio-bench'
RWMIXREAD = 25

# rw modes, iodepths, block sizes and numbers of jobs; a mixed mode can end with its read percentage
# (randrw75: 75 % of reads), RWMIXREAD otherwise
MATRICES: dict[str, tuple[tuple[str, ...], tuple[int, ...], tuple[str, ...], tuple[int, ...]]] = {
    'short': (('randread', 'randwrite', 'randrw'), (1, 64), ('4k', '1M'), (1,)),
    # the matrix of fio-bench-timeseries-all.sh
    'full': (('randread', 'randwrite', 'randrw'), (1, 4, 16, 64, 256), ('4k', '32k', '256k', '1M'), (1, 2)),
    # to tune tapdisk (kick deferral, polling), and its runs with requests in flight only
    'tuning': (('read', 'write', 'rw', 'randrw'), (1, 64), ('4k', '1M'), (1, 4)),
    'tuning-qd64': (('read', 'write', 'rw', 'randrw'), (64,), ('4k', '1M'), (1, 4)),
    'tuning-1M': (('read', 'write', 'rw', 'randrw'), (1, 64), ('1M',), (1, 4)),
    # to compare tapdisk builds on sequential I/O, with a single job or one per queue
    'seq-j1': (('read', 'write'), (1, 64), ('4k', '1M'), (1,)),
    'seq-j4': (('read', 'write'), (1, 64), ('4k', '1M'), (4,)),
    'seq': (('read', 'write'), (1, 64), ('4k', '1M'), (1, 4)),
    # the same, with a random mix of 75 % reads and 25 % writes
    'mix': (('read', 'write', 'randrw75'), (1, 64), ('4k', '1M'), (1, 4)),
}

# runs given one by one: (rw, iodepth, bs, numjobs)
RUN_LISTS: dict[str, list[tuple[str, int, str, int]]] = {
    # the DiskSpd runs of test_win_bench (CrystalDiskMark profiles), to compare Linux and Windows guests:
    # DiskSpd threads and requests in flight per thread are fio jobs and iodepth
    'crystal': [
        ('read', 8, '1M', 1),  # seq1m-q8t1-read
        ('write', 8, '1M', 1),  # seq1m-q8t1-write
        ('read', 32, '128k', 1),  # seq128k-q32t1-read
        ('write', 32, '128k', 1),  # seq128k-q32t1-write
        ('randread', 32, '4k', 4),  # rnd4k-q32t4-read
        ('randwrite', 32, '4k', 4),  # rnd4k-q32t4-write
        ('randread', 1, '4k', 1),  # rnd4k-q1t1-read
        ('randwrite', 1, '4k', 1),  # rnd4k-q1t1-write
        ('randrw70', 16, '8k', 4),  # rnd8k-q16t4-mix70
    ],
}

# context information saved with each run, as fio-bench-timeseries.sh does
CONTEXT_COMMANDS = {
    'info-cmdline.txt': 'cat /proc/cmdline',
    'info-blkfront.txt': "cd /sys/module/xen_blkfront/parameters/ && grep -H '' max_queues",
    'info-xenstore.txt': 'xenstore-ls /',
    'info-queue-params.txt': f"grep . /sys/block/{DEVICE}/queue/*",
}

@dataclass(frozen=True)
class FioRun:
    rw: str
    iodepth: int
    bs: str
    numjobs: int

    @property
    def name(self) -> str:
        # the directory names of fio-bench-timeseries.sh
        return f"{self.rw}-bs{self.bs}-j{self.numjobs}-d{self.iodepth}"

    @property
    def fio_rw(self) -> str:
        return self.rw.rstrip('0123456789')

    @property
    def rwmixread(self) -> int:
        return int(self.rw[len(self.fio_rw):] or RWMIXREAD)

def fio_runs(matrix: str) -> list[FioRun]:
    """The runs of a matrix, in the order of fio-bench-timeseries-all.sh, or of a list of runs."""
    if matrix in RUN_LISTS:
        return [FioRun(*run) for run in RUN_LISTS[matrix]]
    return [FioRun(*run) for run in itertools.product(*MATRICES[matrix])]

def bench_fio_command(run: FioRun, runtime: int, workdir: str) -> str:
    """The fio command of fio-bench-timeseries.sh, with its results in workdir."""
    log_prefix = f'{workdir}/fio-ts'
    return shlex.join([
        'fio', '--name=timeseries', f'--rw={run.fio_rw}', f'--rwmixread={run.rwmixread}', f'--bs={run.bs}',
        f'--numjobs={run.numjobs}', f'--iodepth={run.iodepth}', f'--filename=/dev/{DEVICE}',
        f'--runtime={runtime}', f'--ramp_time={RAMP_TIME_SECS}', '--time_based', '--direct=1',
        '--ioengine=libaio', '--group_reporting', '--randrepeat=1',
        f'--write_bw_log={log_prefix}', f'--write_iops_log={log_prefix}', f'--write_lat_log={log_prefix}',
        f'--log_avg_msec={LOG_INTERVAL_MSECS}', '--per_job_logs=1', '--status-interval=10',
        '--output-format=json', f'--output={workdir}/fio-result.json',
    ])

def save_context(vm: VM, workdir: str) -> None:
    # a missing piece of information (e.g. no xenstore-ls in the guest) leaves an error message in its file
    commands = ' '.join(f'({command}) > {workdir}/{name} 2>&1;' for name, command in CONTEXT_COMMANDS.items())
    vm.ssh(f'{commands} true')

def fetch_dir(vm: VM, guest_dir: str, local_dir: Path) -> None:
    """Copy the content of a guest directory into a local directory, as a single tar stream."""
    archive = vm.ssh(shlex.join(['tar', '-C', guest_dir, '-cf', '-', '.']), decode=False)
    local_dir.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(local_dir, filter='data')

def last_fio_result(path: Path) -> dict[str, Any]:
    """The last JSON object of a fio output: with --status-interval, fio writes one per interval."""
    text = path.read_text()
    decoder = json.JSONDecoder()
    result = None
    position = text.find('{')
    while position != -1:
        result, position = decoder.raw_decode(text, position)
        position = text.find('{', position)
    assert result is not None, \
        f"no JSON object in {path}"
    return result

def summary(fio_result: dict[str, Any]) -> str:
    """Bandwidth, IOPS and mean latency of each direction of a run (one job: --group_reporting)."""
    job = fio_result['jobs'][0]
    return ', '.join(
        f"{direction} {job[direction]['bw'] / 1024:.1f} MiB/s {job[direction]['iops']:.0f} IOPS "
        f"lat {job[direction]['lat_ns']['mean'] / 1000:.0f} us"
        for direction in ('read', 'write') if job[direction]['io_bytes']
    )

@pytest.mark.small_vm
@pytest.mark.unix_vm
# several vCPUs, so that blkfront uses several queues with a qcow2 image: blkfront uses at most one per vCPU
@pytest.mark.parametrize('vm_with_vcpu_count', [4], indirect=True)
@pytest.mark.parametrize('bench_image', ['qcow2', 'vhd'], indirect=True)
def test_fio_bench(host: Host, vm_with_fio: VM, bench_image: str, vbd_connector: VBDConnector,
                   bench_outdir: Path, defer: Defer) -> None:
    """fio runs on a VBD served by tapdisk, with their results kept for the blktap-bench tools."""
    vm = vm_with_fio
    runs = fio_runs(config.blktap_bench_matrix)
    runtime = config.blktap_bench_runtime
    existing = [run.name for run in runs if (bench_outdir / run.name).exists()]
    assert not existing, \
        f"results already in {bench_outdir} for {', '.join(existing)}: choose another --blktap-bench-outdir"

    pid, minor = vbd_connector.connect(vm, bench_image, DEVICE)
    wait_for_guest_device(vm, DEVICE)
    defer(lambda: vm.ssh(f'rm -rf {GUEST_WORKDIR}', check=False))
    prefill(vm, DEVICE)
    duration_min = len(runs) * (runtime + RAMP_TIME_SECS) / 60
    logging.info(f"{len(runs)} fio runs of {runtime} s, about {duration_min:.0f} min: results in {bench_outdir}")

    failures = []
    for index, run in enumerate(runs, start=1):
        logging.info(f"fio run {index}/{len(runs)}: {run.name}")
        workdir = f'{GUEST_WORKDIR}/{run.name}'
        vm.ssh(f'rm -rf {workdir} && mkdir -p {workdir}')
        result = vm.ssh_with_result(bench_fio_command(run, runtime, workdir))
        save_context(vm, workdir)
        fetch_dir(vm, workdir, bench_outdir / run.name)
        stats = vbd_connector.tapctl.stats(pid, minor)
        (bench_outdir / run.name / 'tapdisk-stats.json').write_text(json.dumps(stats, indent=1))
        vm.ssh(f'rm -rf {workdir}')
        if result.returncode != 0:
            failures.append(f"{run.name}: fio exited with {result.returncode}: {result.stdout.strip()}")
            continue
        fio_result = last_fio_result(bench_outdir / run.name / 'fio-result.json')
        job_errors = [job['error'] for job in fio_result['jobs'] if job['error']]
        if job_errors:
            failures.append(f"{run.name}: fio job errors {job_errors}")
            continue
        logging.info(f"{run.name}: {summary(fio_result)}")

    assert not failures, \
        "fio runs failed:\n" + "\n".join(failures)
    # the guest I/O went through the tapdisk of the VBD
    assert_tapdisk_io(host, pid, minor, size_mib=1)
