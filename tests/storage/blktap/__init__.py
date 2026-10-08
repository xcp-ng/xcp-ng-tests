from __future__ import annotations

import pytest

import logging
import shlex
from dataclasses import dataclass

from lib.blktap import TapCtl
from lib.common import MiB, wait_for
from tests.storage.storage import StreamSpan

from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from lib.host import Host
    from lib.vm import VM

@dataclass
class ConnectedVBD:
    vm: VM
    device: str
    image: str  # "<format>:<path>", as given to connect()
    pid: int
    minor: int

def wait_for_guest_device(vm: VM, device: str, present: bool = True) -> None:
    """Wait for /dev/<device> to appear in the guest (or to disappear, if present is False)."""
    wait_for(lambda: (vm.ssh_with_result(f'test -b /dev/{device}').returncode == 0) == present,
             f"Wait for /dev/{device} to {'appear in' if present else 'disappear from'} the guest",
             timeout_secs=30)

def guest_queue_count(vm: VM, device: str) -> int:
    """Number of hardware queues of the device, as seen by the guest block layer."""
    return len(vm.ssh(f'ls /sys/block/{device}/mq').split())

def write_then_read(vm: VM, device: str, size_mib: int) -> None:
    """Write random data to the device, then read it back through tapdisk."""
    vm.ssh(f'dd if=/dev/urandom of=/dev/{device} bs=1M count={size_mib} conv=fsync status=none')
    # drop the guest page cache, so that the read goes through tapdisk
    vm.ssh(f'blockdev --flushbufs /dev/{device}')
    vm.ssh(f'dd if=/dev/{device} of=/dev/null bs=1M count={size_mib} status=none')

def assert_tapdisk_io(host: Host, pid: int, minor: int, size_mib: int) -> None:
    """Check that tapdisk served at least size_mib MiB of reads and of writes."""
    read_secs, written_secs = TapCtl(host).stats(pid, minor)['secs']  # in 512-byte sectors
    expected = size_mib * MiB // 512
    assert written_secs >= expected, \
        f"tapdisk wrote {written_secs} sectors, expected at least {expected}"
    assert read_secs >= expected, \
        f"tapdisk read {read_secs} sectors, expected at least {expected}"

def write_concurrently(vm: VM, devices: list[str], size_mib: int) -> None:
    """Write random data to all the devices at the same time, fail if any write fails."""
    # a bare `wait` always succeeds: wait for each pid to get its exit status
    writes = ' '.join(
        f'dd if=/dev/urandom of=/dev/{device} bs=1M count={size_mib} conv=fsync status=none & pids="$pids $!";'
        for device in devices
    )
    vm.ssh(f'pids=; {writes} for pid in $pids; do wait $pid || exit 1; done')

def assert_tapdisk_destroyed(host: Host, pid: int, minor: int) -> None:
    """Check that neither the tapdisk process nor its minor are still listed by tap-ctl."""
    for option, value in (('-p', pid), ('-m', minor)):
        result = host.ssh_with_result(f'tap-ctl list {option} {value}')
        assert result.returncode != 0 or not result.stdout.strip(), \
            f"tap-ctl list {option} {value} still lists a tapdisk:\n{result.stdout}"

def wait_for_tapdisk_exit(host: Host, pid: int) -> None:
    # The driver refuses to free a minor (EBUSY) as long as its ring is in use (ring->task
    # in drivers/block/blktap2/ring.c), which lasts until the tapdisk exits, even after
    # the detach: the minors can only be freed once the tapdisk exited, after its last detach.
    wait_for(lambda: host.ssh_with_result(f'test -d /proc/{pid}').returncode != 0,
             f"Wait for tapdisk {pid} to exit after its last detach", timeout_secs=30)

FIO_ACCESS_TYPES = ('read', 'rw', 'randrw')
FIO_BLOCK_SIZES = ('4k', '64k', '1m')

def fio_command(device: str, runtime: int, access_types: Sequence[str] = FIO_ACCESS_TYPES,
                block_sizes: Sequence[str] = FIO_BLOCK_SIZES) -> str:
    """fio I/O on the device: one job per access type and block size, all running at the same time."""
    # fio rather than dd: several jobs (processes) and many requests in flight (libaio, iodepth)
    # exercise the multi-queue and multi-thread paths of tapdisk, which matter in these tests.
    # The options before the first --name apply to all the jobs.
    jobs = ' '.join(f'--name={rw}-{bs} --rw={rw} --bs={bs}' for rw in access_types for bs in block_sizes)
    return (f'fio --filename=/dev/{device} --direct=1 --ioengine=libaio --iodepth=64 '
            f'--runtime={runtime} --time_based {jobs}')

def start_fio(vm: VM, device: str, runtime: int, access_types: Sequence[str] = FIO_ACCESS_TYPES,
              block_sizes: Sequence[str] = FIO_BLOCK_SIZES) -> None:
    """Run fio_command in the guest, in the background."""
    vm.ssh(f'setsid {fio_command(device, runtime, access_types, block_sizes)} > /tmp/fio.log 2>&1 < /dev/null &')

def wait_for_guest_reads(host: Host, pid: int, minor: int) -> None:
    wait_for(lambda: TapCtl(host).stats(pid, minor)['secs'][0] >= 10 * MiB // 512,
             "Wait for the guest reads to reach tapdisk", timeout_secs=30)

@dataclass
class RegionWriters:
    """Writers running in the background in the VM, writing regions of a device with randstream."""

    # Each region is written only once, so that a lost write can't be hidden by a later write of
    # the same region: it's meant to check the data written while an operation is running (e.g. a
    # live migration). The writers write the regions first_region to first_region + region_count - 1:
    # writer w writes the regions w, w + writer_count, w + 2 * writer_count... of this range, with a
    # pause between two regions, until all its regions are written or it's asked to stop.
    vm: VM
    dev: str
    region_size: int
    first_region: int
    region_count: int
    writer_count: int
    pause_secs: int
    workdir: str = '/tmp/region-writers'

    def start(self) -> None:
        logging.info(f"Start {self.writer_count} writers on regions {self.first_region} to "
                     f"{self.first_region + self.region_count - 1} of {self.dev}")
        self.vm.ssh(f'rm -rf {self.workdir} && mkdir {self.workdir}')
        end = self.first_region + self.region_count
        for writer in range(self.writer_count):
            # the writer writes its exit status at the end: "ok", or the region it failed to write
            script = (
                f'i={self.first_region + writer}; '
                f'while [ $i -lt {end} ] && [ ! -e {self.workdir}/stop ]; do '
                f'randstream -v generate --seed $((1000 + i)) --position $((i * {self.region_size})) '
                f'--size {self.region_size} {self.dev} > {self.workdir}/region-$i.log 2>&1 '
                f'|| {{ echo $i > {self.workdir}/status-{writer}; exit; }}; '
                f'i=$((i + {self.writer_count})); sleep {self.pause_secs}; '
                f'done; '
                f'echo ok > {self.workdir}/status-{writer}'
            )
            self.vm.ssh(f'setsid sh -c {shlex.quote(script)} > /dev/null 2>&1 < /dev/null &')

    def _stopped_count(self) -> int:
        return int(self.vm.ssh(f'ls {self.workdir} | grep -c "^status-" || true'))

    def assert_running(self) -> None:
        """Fail if a writer stopped: it wrote all its regions too early, or a write failed."""
        for writer in range(self.writer_count):
            result = self.vm.ssh_with_result(f'cat {self.workdir}/status-{writer}')
            if result.returncode != 0:
                continue  # no status yet: still running
            status = result.stdout.strip()
            if status == 'ok':
                pytest.fail(f"Writer {writer} wrote all its regions of {self.dev} before the end of the operation: "
                            "make the writes last longer (more regions, or a longer pause)")
            log = self.vm.ssh(f'cat {self.workdir}/region-{status}.log')
            pytest.fail(f"Writer {writer} failed to write region {status} of {self.dev}:\n{log}")

    def stop(self) -> list[StreamSpan]:
        """Stop the writers once their current region is written, return the written regions."""
        self.vm.ssh(f'touch {self.workdir}/stop')
        wait_for(lambda: self._stopped_count() == self.writer_count,
                 "Wait for the writers to stop", timeout_secs=120)

        for writer in range(self.writer_count):
            status = self.vm.ssh(f'cat {self.workdir}/status-{writer}')
            if status != 'ok':
                log = self.vm.ssh(f'cat {self.workdir}/region-{status}.log')
                pytest.fail(f"Writer {writer} failed to write region {status} of {self.dev}:\n{log}")

        # one "<region> <checksum>" line per region written
        output = self.vm.ssh(
            f'cd {self.workdir} && for f in region-*.log; do '
            f'echo "${{f#region-}} $(sed -n "s/^checksum: //p" "$f")"; done'
        )
        spans = []
        for line in output.splitlines():
            name, _, checksum = line.partition(' ')
            region = int(name.removesuffix('.log'))
            assert checksum, \
                f"No checksum in the randstream output of region {region}"
            spans.append(StreamSpan(position=region * self.region_size, size=self.region_size, checksum=checksum))
        logging.info(f"{len(spans)} regions of {self.dev} written")
        return spans

def validate_spans(vm: VM, dev: str, spans: list[StreamSpan]) -> None:
    """Validate the spans against their checksum, in a single SSH command."""
    logging.info(f"Validate {len(spans)} spans of {dev}")
    lines = ''.join(f'{span.position} {span.size} {span.checksum}\n' for span in spans)
    script = (
        'while read position size checksum; do '
        f'randstream validate --expected-checksum $checksum --position $position --size $size {dev} '
        '|| { echo "span at $position: validation failed"; exit 1; }; '
        'done'
    )
    vm.ssh(f'printf %s {shlex.quote(lines)} | sh -c {shlex.quote(script)}')
