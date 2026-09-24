from __future__ import annotations

from lib.blktap import TapCtl
from lib.common import MiB, wait_for

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host
    from lib.vm import VM

def wait_for_guest_device(vm: VM, device: str, present: bool = True) -> None:
    """Wait for /dev/<device> to appear in the guest (or to disappear, if present is False)."""
    wait_for(lambda: (vm.ssh_with_result(f'test -b /dev/{device}').returncode == 0) == present,
             f"Wait for /dev/{device} to {'appear in' if present else 'disappear from'} the guest",
             timeout_secs=30)

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
