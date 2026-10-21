from __future__ import annotations

import pytest

import json
import logging
import re
import shlex
import time
from pathlib import Path

from lib import config
from lib.blktap import VBDConnector
from lib.common import PackageManagerEnum
from tests.storage.blktap import assert_tapdisk_io, prefill, wait_for_guest_device

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from lib.common import Defer
    from lib.host import Host
    from lib.vm import VM

# Application benchmarks of a qcow2 or VHD VBD served by tapdisk: unlike the fio benchmark, they go through
# a filesystem and its journal, fsync and flushes, and metadata operations. The VBD is written whole once,
# so that no workload pays the allocation of the image blocks, then each workload runs on a new ext4
# filesystem of the VBD, and leaves its output, its metrics (result.json) and the CPU time of the tapdisk
# threads and of dom0 during the workload (cpu.json) in <--blktap-bench-outdir>/<format>/<workload>/.
# There is no performance threshold: the test only fails when a workload fails, or when the I/O didn't go
# through tapdisk.
#
# The workloads are chosen with --blktap-app-bench-workloads. pgbench and filebench run for
# --blktap-bench-runtime seconds; sqlite-* and kernel run to completion.
#
# Requirements:
# - an XCP-ng host with blktap and tapback, with 10 GiB free in dom0 /tmp (one image at a time)
# - a Debian 12 VM, with internet access to install the tools and download the sources
#   (cloned with 8 vCPUs and 4 GiB of RAM by this test)

DEVICE = "xvdb"
MOUNTPOINT = '/mnt/bench'
VCPU_COUNT = 8
MEMORY = '4GiB'
# pgbench and filebench run in a cgroup limited to this memory, page cache included: their data set
# is larger, so that they measure the disk rather than the guest page cache
CGROUP_MEMORY_MAX = '1G'
# the guest may not have the locales of the test runner, sent by SSH (SendEnv)
GUEST_ENV = 'export LC_ALL=C.UTF-8;'

PGBENCH_SCALE = 100  # about 1.5 GB
PGBENCH_CLIENTS = 8
FILEBENCH_URL = 'https://github.com/filebench/filebench/releases/download/1.5-alpha3/filebench-1.5-alpha3.tar.gz'
FILEBENCH_WORKLOADS = '/usr/local/share/filebench/workloads'
# variables of the filebench personalities changed from their defaults: a webserver file set of 1.6 GB
# rather than 16 MB, larger than CGROUP_MEMORY_MAX
FILEBENCH_SETTINGS = {
    'fileserver': {},
    'varmail': {},
    'webserver': {'nfiles': '100000'},
}
SQLITE_SPEEDTEST1_URL = 'https://raw.githubusercontent.com/sqlite/sqlite/version-{version}/test/speedtest1.c'
SQLITE_SIZE = 100
# an old LTS kernel, which still builds with the GCC 12 of Debian 12
KERNEL_VERSION = '5.10.271'
KERNEL_URL = f'https://cdn.kernel.org/pub/linux/kernel/v5.x/linux-{KERNEL_VERSION}.tar.xz'
KERNEL_TARBALL = f'/root/linux-{KERNEL_VERSION}.tar.xz'

APT_PACKAGES = ['build-essential', 'bc', 'bison', 'flex', 'libelf-dev', 'libssl-dev', 'xz-utils', 'curl',
                'ca-certificates', 'postgresql', 'libsqlite3-dev', 'fio']

def guest_run(vm: VM, command: str) -> str:
    return vm.ssh(f'{GUEST_ENV} {command}')

def timed(vm: VM, command: str) -> float:
    """Run a guest command, return its duration in seconds."""
    start = time.monotonic()
    guest_run(vm, command)
    return time.monotonic() - start

def cpu_snapshot(host: Host, pid: int) -> dict[str, int]:
    """CPU time (clock ticks) of each tapdisk thread ("<name>/<tid>") and of dom0 ("dom0-busy", "dom0-total")."""
    # utime and stime are the 14th and 15th fields of stat, the 12th and 13th after the command name
    output = host.ssh(f"for t in /proc/{pid}/task/*; do echo $(cat $t/comm)/${{t##*/}} "
                      f"$(sed 's/.*) //' $t/stat | cut -d' ' -f12,13); done; head -n 1 /proc/stat")
    ticks = {}
    for line in output.splitlines():
        if line.startswith('cpu '):
            values = [int(value) for value in line.split()[1:]]
            ticks['dom0-total'] = sum(values)
            ticks['dom0-busy'] = sum(values) - values[3] - values[4]  # minus idle and iowait
            continue
        # thread names may contain spaces (IOThreads); a thread that just exited has no ticks
        fields = line.rsplit(maxsplit=2)
        if len(fields) == 3 and fields[1].isdigit() and fields[2].isdigit():
            ticks[fields[0]] = int(fields[1]) + int(fields[2])
    return ticks

def cpu_usage(before: dict[str, int], after: dict[str, int], wall_secs: float) -> dict[str, object]:
    """CPU seconds of each tapdisk thread between two snapshots, and dom0 busy percentage."""
    tick_secs = 0.01  # USER_HZ
    threads = {name: (ticks - before.get(name, 0)) * tick_secs for name, ticks in after.items()
               if not name.startswith('dom0-')}
    total = after['dom0-total'] - before['dom0-total']
    return {
        'wall_secs': wall_secs,
        'tapdisk_cpu_secs': sum(threads.values()),
        'tapdisk_cpu_pct': sum(threads.values()) / wall_secs * 100,
        'dom0_busy_pct': (after['dom0-busy'] - before['dom0-busy']) / total * 100 if total else 0.0,
        'threads': threads,
    }

def make_filesystem(vm: VM) -> None:
    """A new ext4 filesystem on the VBD, mounted, with an empty guest page cache."""
    guest_run(vm, f'umount {MOUNTPOINT} 2>/dev/null; mkfs.ext4 -q -F /dev/{DEVICE} && mkdir -p {MOUNTPOINT} '
                  f'&& mount /dev/{DEVICE} {MOUNTPOINT} && sync && echo 3 > /proc/sys/vm/drop_caches')

def unmount(vm: VM) -> None:
    guest_run(vm, f'umount {MOUNTPOINT}')

def install_tools(vm: VM) -> None:
    """The packages, filebench and speedtest1 (built from their sources) and the kernel tarball.

    What is already there is kept: tools installed in the VM given with --vm save the time of each clone.
    """
    package_manager = vm.detect_package_manager()
    assert package_manager == PackageManagerEnum.APT_GET, \
        f"the application benchmark needs a Debian VM, not a VM with {package_manager}"
    missing = vm.ssh_with_result(f"dpkg-query -W {shlex.join(APT_PACKAGES)} 2>&1 | grep -q 'no packages found'")
    if missing.returncode == 0:
        guest_run(vm, 'apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q '
                      + shlex.join(APT_PACKAGES))
    # the PostgreSQL cluster is created on the VBD by run_pgbench
    guest_run(vm, 'if pg_lsclusters -h | grep -q "^15 main "; then pg_dropcluster --stop 15 main; fi')
    guest_run(vm, f'command -v filebench || (cd /root && curl -sSfL {FILEBENCH_URL} | tar xz && cd filebench-* '
                  '&& ./configure -q && make -s -j8 && make -s install)')
    # filebench needs a fixed address space layout for its shared memory
    guest_run(vm, 'echo 0 > /proc/sys/kernel/randomize_va_space')
    sqlite_version = guest_run(vm, "dpkg-query -W -f '${Version}' libsqlite3-dev").split('-')[0]
    speedtest1_url = SQLITE_SPEEDTEST1_URL.format(version=sqlite_version)
    guest_run(vm, f'test -x /root/speedtest1 || (cd /root && curl -sSfO {speedtest1_url} '
                  '&& gcc -O2 -o speedtest1 speedtest1.c -lsqlite3)')
    guest_run(vm, f'test -s {KERNEL_TARBALL} || curl -sSf -o {KERNEL_TARBALL} {KERNEL_URL}')

def run_pgbench(vm: VM, outdir: Path) -> dict[str, float]:
    """TPC-B like transactions of pgbench, PostgreSQL data on the VBD."""
    pgdata = f'{MOUNTPOINT}/pg'
    guest_run(vm, f'mkdir -p {pgdata} && chown postgres: {pgdata} && pg_createcluster 15 bench -d {pgdata}/data')
    try:
        guest_run(vm, f'systemctl set-property --runtime postgresql@15-bench.service '
                      f'MemoryMax={CGROUP_MEMORY_MAX} && systemctl start postgresql@15-bench')
        init = guest_run(vm, f"su postgres -c 'createdb bench && pgbench -q -i -s {PGBENCH_SCALE} bench' 2>&1")
        guest_run(vm, 'sync && echo 3 > /proc/sys/vm/drop_caches')
        output = guest_run(vm, f"su postgres -c 'pgbench -c {PGBENCH_CLIENTS} -j {PGBENCH_CLIENTS} "
                               f"-T {config.blktap_bench_runtime} bench' 2>&1")
    finally:
        guest_run(vm, 'pg_dropcluster --stop 15 bench')
    (outdir / 'output.txt').write_text(init + '\n' + output)
    tps = re.search(r'^tps = ([\d.]+)', output, re.MULTILINE)
    latency = re.search(r'^latency average = ([\d.]+) ms', output, re.MULTILINE)
    assert tps and latency, \
        f"no tps or latency in the pgbench output:\n{output}"
    return {'tps': float(tps.group(1)), 'latency_ms': float(latency.group(1))}

def run_sqlite(vm: VM, outdir: Path, journal: str) -> dict[str, float]:
    """speedtest1, the SQLite benchmark, with a journal mode."""
    output = guest_run(vm, f'/root/speedtest1 --size {SQLITE_SIZE} --journal {journal} {MOUNTPOINT}/speedtest1.db')
    (outdir / 'output.txt').write_text(output)
    total = re.search(r'TOTAL\.+\s+([\d.]+)s', output)
    assert total, \
        f"no total time in the speedtest1 output:\n{output}"
    return {'total_secs': float(total.group(1))}

def run_filebench(vm: VM, outdir: Path, personality: str) -> dict[str, float]:
    """A filebench personality, in a memory-limited cgroup, with its file set on the VBD."""
    settings = {'dir': f'{MOUNTPOINT}/filebench', **FILEBENCH_SETTINGS[personality]}
    sed = [f's|^set \\${name}=.*|set \\${name}={value}|' for name, value in settings.items()]
    sed.append(f's|^run [0-9]*|run {config.blktap_bench_runtime}|')
    workload = f'/root/{personality}.f'
    guest_run(vm, f"mkdir -p {settings['dir']} && sed {' '.join(f'-e {shlex.quote(e)}' for e in sed)} "
                  f'{FILEBENCH_WORKLOADS}/{personality}.f > {workload}')
    output = guest_run(vm, f'systemd-run --scope -q -p MemoryMax={CGROUP_MEMORY_MAX} filebench -f {workload}')
    (outdir / 'output.txt').write_text(output)
    summary = re.search(r'IO Summary: (\d+) ops ([\d.]+) ops/s (\d+)/(\d+) rd/wr\s+([\d.]+)mb/s\s+([\d.]+)ms/op',
                        output)
    assert summary, \
        f"no IO Summary in the filebench output:\n{output}"
    return {'ops_per_sec': float(summary.group(2)), 'mb_per_sec': float(summary.group(5)),
            'ms_per_op': float(summary.group(6))}

def run_kernel(vm: VM, outdir: Path) -> dict[str, float]:
    """Extract and build a Linux kernel (defconfig) on the VBD."""
    src = f'{MOUNTPOINT}/linux-{KERNEL_VERSION}'
    metrics = {
        'extract_secs': timed(vm, f'tar -C {MOUNTPOINT} -xf {KERNEL_TARBALL} && sync'),
        'defconfig_secs': timed(vm, f'make -s -C {src} defconfig'),
    }
    start = time.monotonic()
    result = vm.ssh_with_result(f'{GUEST_ENV} make -s -C {src} -j{VCPU_COUNT} > {MOUNTPOINT}/build.log 2>&1 '
                                f'&& sync')
    metrics['build_secs'] = time.monotonic() - start
    (outdir / 'output.txt').write_text(guest_run(vm, f'tail -n 50 {MOUNTPOINT}/build.log'))
    assert result.returncode == 0, \
        f"the kernel build failed: see {outdir / 'output.txt'}"
    return metrics

RUNNERS: dict[str, Callable[[VM, Path], dict[str, float]]] = {
    'pgbench': run_pgbench,
    'sqlite-delete': lambda vm, outdir: run_sqlite(vm, outdir, 'delete'),
    'sqlite-wal': lambda vm, outdir: run_sqlite(vm, outdir, 'wal'),
    'fileserver': lambda vm, outdir: run_filebench(vm, outdir, 'fileserver'),
    'varmail': lambda vm, outdir: run_filebench(vm, outdir, 'varmail'),
    'webserver': lambda vm, outdir: run_filebench(vm, outdir, 'webserver'),
    'kernel': run_kernel,
}

@pytest.fixture
def vm_with_app_tools(vm_with_vcpu_count: VM) -> VM:
    """The (halted) VM of vm_with_vcpu_count, with MEMORY of RAM, started, with the benchmark tools."""
    vm = vm_with_vcpu_count
    vm.host.xe('vm-memory-limits-set', {'uuid': vm.uuid, 'static-min': MEMORY, 'static-max': MEMORY,
                                        'dynamic-min': MEMORY, 'dynamic-max': MEMORY})
    vm.start()
    vm.wait_for_vm_running_and_ssh_up()
    install_tools(vm)
    return vm
    # no teardown: vm_with_vcpu_count destroys the VM

@pytest.mark.big_vm
@pytest.mark.unix_vm
@pytest.mark.parametrize('vm_with_vcpu_count', [VCPU_COUNT], indirect=True)
@pytest.mark.parametrize('app_bench_image', ['qcow2', 'vhd'], indirect=True)
def test_app_bench(host: Host, vm_with_app_tools: VM, app_bench_image: str, vbd_connector: VBDConnector,
                   app_bench_outdir: Path, defer: Defer) -> None:
    """Application workloads on a filesystem of a VBD served by tapdisk, with their results kept."""
    vm = vm_with_app_tools
    workloads = config.blktap_app_bench_workloads
    unknown = [workload for workload in workloads if workload not in RUNNERS]
    assert not unknown, \
        f"unknown workloads {', '.join(unknown)}: choose among {', '.join(RUNNERS)}"
    existing = [workload for workload in workloads if (app_bench_outdir / workload).exists()]
    assert not existing, \
        f"results already in {app_bench_outdir} for {', '.join(existing)}: choose another --blktap-bench-outdir"

    pid, minor = vbd_connector.connect(vm, app_bench_image, DEVICE)
    wait_for_guest_device(vm, DEVICE)
    defer(lambda: vm.ssh(f'umount {MOUNTPOINT}', check=False))
    prefill(vm, DEVICE)

    failures = []
    for index, workload in enumerate(workloads, start=1):
        logging.info(f"workload {index}/{len(workloads)}: {workload}")
        outdir = app_bench_outdir / workload
        outdir.mkdir()
        make_filesystem(vm)
        cpu_before = cpu_snapshot(host, pid)
        start = time.monotonic()
        try:
            metrics = RUNNERS[workload](vm, outdir)
        except Exception as e:
            failures.append(f"{workload}: {e}")
            continue
        finally:
            unmount(vm)
        cpu = cpu_usage(cpu_before, cpu_snapshot(host, pid), time.monotonic() - start)
        (outdir / 'cpu.json').write_text(json.dumps(cpu, indent=1))
        (outdir / 'result.json').write_text(json.dumps(metrics, indent=1))
        (outdir / 'tapdisk-stats.json').write_text(json.dumps(vbd_connector.tapctl.stats(pid, minor), indent=1))
        logging.info(f"{workload}: " + ', '.join(f'{name} {value:.1f}' for name, value in metrics.items())
                     + f", tapdisk CPU {cpu['tapdisk_cpu_pct']:.0f} %, dom0 busy {cpu['dom0_busy_pct']:.0f} %")

    assert not failures, \
        "workloads failed:\n" + "\n".join(failures)
    # the guest I/O went through the tapdisk of the VBD
    assert_tapdisk_io(host, pid, minor, size_mib=1)
