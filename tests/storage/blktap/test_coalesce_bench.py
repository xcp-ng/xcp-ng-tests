from __future__ import annotations

import pytest

import json
import logging
import shlex
import statistics
import tempfile
import time
from pathlib import Path

from lib import config
from lib.common import MiB, wait_for
from lib.sr import SR
from tests.storage.blktap import prefill, set_guest_scheduler, wait_for_guest_device
from tests.storage.blktap.test_fio_bench import (
    GUEST_WORKDIR,
    LOG_INTERVAL_MSECS,
    FioRun,
    fetch_dir,
    last_fio_result,
    save_context,
)

from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from lib.common import Defer
    from lib.host import Host
    from lib.vdi import VDI, ImageFormat
    from lib.vm import VM

# fio reads of a VDI while the GC coalesces it: the VDI of a local SR is filled, read once by fio (before),
# snapshotted, written again (--blktap coalesce-bench-size MiB), then fio reads it again while the GC merges it
# into its parent, once the snapshot is destroyed (during). The same fio reads the VDI on top of its snapshot
# for --blktap bench-runtime seconds before the snapshot is destroyed (snapshot), the reference of the reads
# during the coalesce, as the reads go through the chain. The coalesce is the real one: the GC of the SR, as
# in production. fio runs until the coalesce is done, then its bandwidth, IOPS and latency are compared over
# the same fio job in the three phases. There is no performance threshold: the test only fails when fio fails, or
# when the coalesce doesn't happen under the reads.
#
# The format of the VDI is chosen with --image-format (vhd by default), the SR with --blktap coalesce-bench-sr,
# the length of the "before" run with --blktap bench-runtime, the I/O scheduler of the guest with
# --blktap bench-scheduler (mq-deadline by default here), the results directory with --blktap bench-outdir.
# The run to do is chosen with -k, e.g. -k read-bs4k-j1-d32. With --blktap coalesce-bench-burst, fio reads in
# bursts, as a guest that leaves the storage idle at times.
#
# Requirements:
# - an XCP-ng host with a local SR with at least twice --blktap coalesce-bench-size free
# - a small unix VM, with internet access to install fio (cloned with 4 vCPUs by this test)

DEVICE_LOG_PREFIX = 'fio-ts'
SCHEDULER = 'mq-deadline'
RW = 'read'
# the runs of the coalesce benchmark: block sizes, iodepths and numbers of jobs
RUNS = [FioRun(RW, iodepth, bs, numjobs)
        for bs in ('4k', '32k', '1M') for iodepth in (1, 32) for numjobs in (1, 2)]
# fio keeps reading until it's interrupted, when the coalesce is done
FIO_MAX_RUNTIME_SECS = 3600
# fio writes its logs when it ends: its status, every STATUS_INTERVAL_SECS, tells that it reads
STATUS_INTERVAL_SECS = 5
COALESCE_TIMEOUT_SECS = 10 * 60

def fio_command(run: FioRun, runtime: int, workdir: str, device: str, burst_ios: int = 0) -> str:
    """The fio of the benchmark, with its results and per-second logs in workdir.

    With burst_ios, each job pauses --blktap coalesce-bench-burst seconds after that many I/O.
    """
    log_prefix = f'{workdir}/{DEVICE_LOG_PREFIX}'
    burst = config.blktap.coalesce_bench_burst
    burst_args = [f'--thinktime={burst}s', f'--thinktime_blocks={burst_ios}'] if burst_ios else []
    return shlex.join([
        'fio', '--name=coalesce', f'--rw={run.fio_rw}', f'--bs={run.bs}', f'--numjobs={run.numjobs}',
        f'--iodepth={run.iodepth}', f'--filename=/dev/{device}', f'--runtime={runtime}', '--time_based',
        '--direct=1', '--ioengine=libaio', '--group_reporting', '--randrepeat=1',
        f'--write_bw_log={log_prefix}', f'--write_iops_log={log_prefix}', f'--write_lat_log={log_prefix}',
        f'--log_avg_msec={LOG_INTERVAL_MSECS}', '--per_job_logs=1', f'--status-interval={STATUS_INTERVAL_SECS}',
        '--output-format=json', f'--output={workdir}/fio-result.json', *burst_args,
    ])

def per_second(directory: Path, kind: str) -> dict[int, float]:
    """A fio log (bw in KiB/s, iops, lat in ns) by second, bw and iops summed over the jobs, lat averaged."""
    by_job: list[dict[int, float]] = []
    for path in sorted(directory.glob(f'{DEVICE_LOG_PREFIX}_{kind}.*.log')):
        samples: dict[int, list[float]] = {}
        for line in path.read_text().splitlines():
            time_ms, value = line.split(',')[:2]
            samples.setdefault(round(int(time_ms) / 1000), []).append(float(value))
        # a log has more than one sample in the second where fio is interrupted
        by_job.append({second: statistics.mean(values) for second, values in samples.items()})
    combine = statistics.mean if kind == 'lat' else sum
    return {second: combine(job[second] for job in by_job if second in job)
            for second in sorted(set().union(*by_job))}

def measures(directory: Path, start: float, end: float) -> dict[str, float]:
    """Bandwidth, IOPS and latency over the seconds in ]start, end] of the logs of a fio run.

    The mean latency is weighted by the IOPS of each second; its maximum is the one of the mean latencies
    of the seconds, which shows the pauses of the I/O (fio logs no IOPS for a second without completed request).
    """
    bw, iops, lat = (per_second(directory, kind) for kind in ('bw', 'iops', 'lat'))
    seconds = [second for second in sorted(iops) if start < second <= end and second in bw and second in lat]
    assert seconds, \
        f"no sample in {directory} between {start:.0f} s and {end:.0f} s"
    total_iops = sum(iops[second] for second in seconds)
    return {
        'bw_mib_s': statistics.mean(bw[second] for second in seconds) / 1024,
        'iops': statistics.mean(iops[second] for second in seconds),
        'lat_us': sum(lat[second] * iops[second] for second in seconds) / total_iops / 1000,
        'lat_max_us': max(latency for second, latency in lat.items() if start < second <= end) / 1000,
    }

def describe(values: dict[str, float]) -> str:
    return (f"{values['bw_mib_s']:.1f} MiB/s {values['iops']:.0f} IOPS lat {values['lat_us']:.0f} us "
            f"(max {values['lat_max_us']:.0f} us)")

@pytest.fixture
def coalesce_sr(host: Host) -> SR:
    if config.blktap.coalesce_bench_sr:
        return SR(config.blktap.coalesce_bench_sr, host.pool)
    srs = host.local_vm_srs()
    assert srs, \
        f"no local SR on {host}: choose one with --blktap coalesce-bench-sr"
    return srs[0]

@pytest.fixture
def coalesce_vdi(coalesce_sr: SR, image_format: ImageFormat) -> Generator[VDI, None, None]:
    vdi = coalesce_sr.create_vdi('coalesce-bench', virtual_size=config.blktap.coalesce_bench_size * MiB,
                                 image_format=image_format)
    yield vdi
    vdi.destroy()

@pytest.fixture
def coalesce_outdir(image_format: ImageFormat) -> Path:
    base = Path(config.blktap.bench_outdir or tempfile.mkdtemp(prefix='blktap-coalesce-'))
    outdir = base / 'coalesce' / image_format
    outdir.mkdir(parents=True, exist_ok=True)
    return outdir

def wait_for_fio_exit(vm: VM) -> None:
    wait_for(lambda: vm.ssh_with_result('pgrep -x fio').returncode != 0,
             "Wait for fio to exit", timeout_secs=60)

@pytest.mark.small_vm
@pytest.mark.unix_vm
# several vCPUs, so that blkfront uses several queues with a qcow2 image: blkfront uses at most one per vCPU
@pytest.mark.parametrize('vm_with_vcpu_count', [4], indirect=True)
@pytest.mark.parametrize('run', RUNS, ids=[run.name for run in RUNS])
def test_read_during_coalesce(host: Host, vm_with_fio: VM, coalesce_vdi: VDI, coalesce_outdir: Path,
                              run: FioRun, defer: Defer) -> None:
    """fio reads a VDI, then the same fio reads it again while the GC coalesces the writes made after a snapshot."""
    vm = vm_with_fio
    outdir = coalesce_outdir / run.name
    assert not outdir.exists(), \
        f"results already in {outdir}: choose another --blktap bench-outdir"
    runtime = config.blktap.bench_runtime
    size_mib = config.blktap.coalesce_bench_size

    vbd = vm.connect_vdi(coalesce_vdi)
    defer(lambda: vm.disconnect_vdi(coalesce_vdi))
    # the VBD can't be unplugged while the guest uses it
    defer(lambda: vm.ssh('pkill -x fio', check=False))
    device = vbd.param_get('device')
    assert device, \
        f"No device for VBD {vbd.uuid}"
    wait_for_guest_device(vm, device)
    set_guest_scheduler(vm, device, config.blktap.bench_scheduler or SCHEDULER)
    defer(lambda: vm.ssh(f'rm -rf {GUEST_WORKDIR}', check=False))
    prefill(vm, device)

    # before: the VDI is the base image, with all its data
    before_dir = f'{GUEST_WORKDIR}/before'
    vm.ssh(f'rm -rf {before_dir} && mkdir -p {before_dir}')
    result = vm.ssh_with_result(fio_command(run, runtime, before_dir, device))
    assert result.returncode == 0, \
        f"fio failed before the snapshot: {result.stdout.strip()}"
    save_context(vm, before_dir)
    fetch_dir(vm, before_dir, outdir / 'before')
    before = measures(outdir / 'before', 0, runtime)
    logging.info(f"{run.name} before the snapshot: {describe(before)}")
    # the fio of Alpine has no --thinktime_iotime: the I/O of a burst are counted with the IOPS before
    burst = config.blktap.coalesce_bench_burst
    burst_ios = max(1, round(before['iops'] / run.numjobs * burst)) if burst else 0

    # the writes after the snapshot are what the GC merges into the base image, when the snapshot is destroyed
    snapshot: VDI | None = coalesce_vdi.snapshot()
    defer(lambda: snapshot.destroy() if snapshot is not None else None)
    vm.ssh(shlex.join(['fio', '--name=rewrite', '--rw=write', '--bs=1M', '--iodepth=32', '--direct=1',
                       '--ioengine=libaio', f'--filename=/dev/{device}', f'--size={size_mib}M']))

    # during: the same fio, until the coalesce is done
    during_dir = f'{GUEST_WORKDIR}/during'
    vm.ssh(f'rm -rf {during_dir} && mkdir -p {during_dir}')
    vm.ssh(f'setsid nohup {fio_command(run, FIO_MAX_RUNTIME_SECS, during_dir, device, burst_ios)} '
           f'> /dev/null 2>&1 < /dev/null &')
    fio_started = time.monotonic()
    wait_for(lambda: vm.ssh_with_result(f'test -s {during_dir}/fio-result.json').returncode == 0,
             "Wait for the first status of fio", timeout_secs=60)
    # snapshot: the reads go through the chain, as during the coalesce
    time.sleep(max(0.0, runtime - (time.monotonic() - fio_started)))

    destroyed = time.monotonic() - fio_started
    # the coalesce is waited for as the tests of the SRs do: the leaf VDI loses its parent
    assert snapshot is not None
    coalesce_vdi.wait_for_coalesce(snapshot.destroy)
    snapshot = None
    coalesced = time.monotonic() - fio_started
    assert vm.ssh_with_result('pgrep -x fio').returncode == 0, \
        "fio ended before the end of the coalesce"
    vm.ssh('pkill -INT -x fio')
    wait_for_fio_exit(vm)
    save_context(vm, during_dir)
    fetch_dir(vm, during_dir, outdir / 'during')
    sr_log = host.ssh_with_result("grep -a SMGC /var/log/SMlog | tail -n 200").stdout
    (outdir / 'smgc.log').write_text(sr_log)

    fio_result = last_fio_result(outdir / 'during' / 'fio-result.json')
    job_errors = [job['error'] for job in fio_result['jobs'] if job['error']]
    assert not job_errors, \
        f"fio job errors during the coalesce: {job_errors}"
    on_snapshot = measures(outdir / 'during', 0, destroyed)
    logging.info(f"{run.name} on the snapshot: {describe(on_snapshot)}")
    during = measures(outdir / 'during', destroyed, coalesced)
    coalesce_secs = coalesced - destroyed
    logging.info(f"{run.name} during the coalesce ({coalesce_secs:.0f} s): {describe(during)}")
    ratios = {name: during[name] / before[name] for name in ('bw_mib_s', 'iops', 'lat_us')}
    logging.info(f"{run.name} during / before: bandwidth {ratios['bw_mib_s']:.2f}, IOPS {ratios['iops']:.2f}, "
                 f"latency {ratios['lat_us']:.2f}")
    snapshot_ratios = {name: during[name] / on_snapshot[name] for name in ('bw_mib_s', 'iops', 'lat_us')}
    logging.info(f"{run.name} during / on the snapshot: bandwidth {snapshot_ratios['bw_mib_s']:.2f}, "
                 f"IOPS {snapshot_ratios['iops']:.2f}, latency {snapshot_ratios['lat_us']:.2f}")
    (outdir / 'coalesce.json').write_text(json.dumps({
        'run': run.name, 'image_format': coalesce_vdi.get_image_format(), 'size_mib': size_mib,
        'scheduler': config.blktap.bench_scheduler or SCHEDULER,
        'snapshot_destroyed_at_secs': destroyed, 'coalesced_at_secs': coalesced, 'coalesce_secs': coalesce_secs,
        'before': before, 'snapshot': on_snapshot, 'during': during, 'during_over_before': ratios,
        'during_over_snapshot': snapshot_ratios,
    }, indent=1))
