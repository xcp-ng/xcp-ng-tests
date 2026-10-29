from __future__ import annotations

import pytest

import logging
import tempfile
import uuid
from pathlib import Path

from lib import config
from lib.blktap import TapCtl, TapCtlError, VBDConnector, XenStoreHelper
from lib.common import PackageManagerEnum
from tests.storage.blktap import ConnectedVBD, wait_for_guest_device, wait_for_tapdisk_exit

from typing import TYPE_CHECKING, Callable, Generator, assert_never

if TYPE_CHECKING:
    from lib.host import Host
    from lib.vm import VM

def _create_image(host: Host, image_format: str, size_mib: int = 100, options: str | None = None,
                  directory: str = '/tmp') -> str:
    """Create an image in a dom0 directory, with qemu-img options (-o) except for VHD, return its path."""
    path = f"{directory}/blktap-test-{uuid.uuid4()}.{image_format}"
    if image_format == 'vhd':
        assert options is None, "vhd-util takes no qemu-img options"
        host.ssh(f'vhd-util create -n {path} -s {size_mib}')
    else:
        qemu_options = f'-o {options} ' if options else ''
        host.ssh(f'qemu-img create -f {image_format} {qemu_options}{path} {size_mib}M')
    return path

@pytest.fixture
def image(request: pytest.FixtureRequest, host: Host) -> Generator[str, None, None]:
    """A 100 MiB image, as "<format>:<path>", in the format given by an indirect parametrization."""
    image_format = request.param
    path = _create_image(host, image_format)
    yield f"{image_format}:{path}"
    host.ssh(f'rm -f {path}')

@pytest.fixture
def images(request: pytest.FixtureRequest, host: Host) -> Generator[list[str], None, None]:
    """Three images like image, in the format given by an indirect parametrization."""
    image_format = request.param
    paths = [_create_image(host, image_format) for _ in range(3)]
    yield [f"{image_format}:{path}" for path in paths]
    host.ssh(f"rm -f {' '.join(paths)}")

BENCH_LV_NAME = 'pytest-bench'

def _raw_bench_image(host: Host, size_mib: int) -> Generator[str, None, None]:
    """A raw image for the aio driver: the LV pytest-bench of --blktap bench-image-vg, else a file in the image dir."""
    vg = config.blktap.bench_image_vg
    if vg:
        path = f'/dev/{vg}/{BENCH_LV_NAME}'
        assert host.ssh_with_result(f'test -e {path}').returncode != 0, \
            f"{path} already exists: remove this LV, which is the one allocated by the test"
        host.ssh(f'lvcreate -y -L {size_mib}M -n {BENCH_LV_NAME} {vg}')
        yield f"aio:{path}"
        host.ssh(f'lvremove -f {path}')
        return
    # the guest writes the whole device before the runs (prefill): no need to write the file here
    path = f"{config.blktap.bench_image_dir}/blktap-test-{uuid.uuid4()}.raw"
    host.ssh(f'fallocate -l {size_mib}M {path}')
    yield f"aio:{path}"
    host.ssh(f'rm -f {path}')

def _bench_image(host: Host, image_format: str, size_mib: int) -> Generator[str, None, None]:
    """Image like setup-qcow2.sh or setup-vhd.sh (qcow2 metadata preallocated), in --blktap bench-image-dir."""
    if image_format == 'raw':
        yield from _raw_bench_image(host, size_mib)
        return
    options = 'preallocation=metadata' if image_format == 'qcow2' else None
    # the tapdisk driver of the image: --blktap bench-qcow2-driver for qcow2
    driver = config.blktap.bench_qcow2_driver if image_format == 'qcow2' else image_format
    device = config.blktap.bench_image_dir
    if host.ssh_with_result(f'test -b {device}').returncode == 0:
        # a block device (e.g. an LV, as on an LVM SR): the image is written on it
        if image_format == 'qcow2':
            host.ssh(f'qemu-img create -f qcow2 -o {options} {device} {size_mib}M')
        else:
            host.ssh(f'vhd-util create -n {device} -s {size_mib}')
        yield f"{driver}:{device}"
        return
    path = _create_image(host, image_format, size_mib=size_mib, options=options,
                         directory=config.blktap.bench_image_dir)
    yield f"{driver}:{path}"
    host.ssh(f'rm -f {path}')

@pytest.fixture
def bench_image(request: pytest.FixtureRequest, host: Host) -> Generator[str, None, None]:
    """Image of the fio benchmark, format by indirect param."""
    # 4 GiB by default rather than the 10 GiB of blktap-bench: dom0 /tmp is on the small root filesystem,
    # but a raw image is an LV or in a directory chosen for it
    default_size = 10240 if request.param == 'raw' else 4096
    yield from _bench_image(host, request.param, config.blktap.bench_image_size or default_size)

@pytest.fixture
def app_bench_image(request: pytest.FixtureRequest, host: Host) -> Generator[str, None, None]:
    """Image of the application benchmark, format by indirect param: 10 GiB, for the kernel build."""
    yield from _bench_image(host, request.param, 10240)

def _bench_outdir(image: str) -> Path:
    """<bench-outdir>/<image format>, or the same in a new temporary directory, kept after the test."""
    base = Path(config.blktap.bench_outdir or tempfile.mkdtemp(prefix='blktap-bench-'))
    driver = image.split(':')[0]
    outdir = base / ('raw' if driver == 'aio' else driver)
    outdir.mkdir(parents=True, exist_ok=True)
    return outdir

@pytest.fixture
def bench_outdir(bench_image: str) -> Path:
    """Results directory of the benchmarks of bench_image."""
    return _bench_outdir(bench_image)

@pytest.fixture
def app_bench_outdir(app_bench_image: str) -> Path:
    """Results directory of the benchmarks of app_bench_image."""
    return _bench_outdir(app_bench_image)

class TrackingTapCtl(TapCtl):
    """TapCtl remembering what it set up, to undo at teardown what the test left behind."""

    def __init__(self, host: Host) -> None:
        super().__init__(host)
        self.spawned: list[int] = []
        self.minors: list[int] = []  # allocated and not freed yet
        self.attached: dict[int, int] = {}  # minor -> pid
        self.opened: dict[int, int] = {}  # minor -> pid

    def spawn(self) -> int:
        pid = super().spawn()
        self.spawned.append(pid)
        return pid

    def allocate(self) -> tuple[int, str]:
        minor, device = super().allocate()
        self.minors.append(minor)
        return minor, device

    def attach(self, pid: int, minor: int) -> None:
        super().attach(pid, minor)
        self.attached[minor] = pid

    def open(self, pid: int, minor: int, path: str, readonly: bool = False, no_o_direct: bool = False,
             timeout: int | None = None, log: str | None = None) -> None:
        super().open(pid, minor, path, readonly, no_o_direct, timeout, log)
        self.opened[minor] = pid

    def close(self, pid: int, minor: int, force: bool = False, timeout: int | None = None) -> None:
        super().close(pid, minor, force, timeout)
        self.opened.pop(minor, None)

    def detach(self, pid: int, minor: int) -> None:
        super().detach(pid, minor)
        self.attached.pop(minor, None)

    def free(self, minor: int) -> None:
        super().free(minor)
        if minor in self.minors:
            self.minors.remove(minor)

    def create(self, path: str, readonly: bool = False, log: str | None = None) -> tuple[int, int]:
        pid, minor = super().create(path, readonly, log)
        self.spawned.append(pid)
        self.minors.append(minor)
        self.attached[minor] = pid
        self.opened[minor] = pid
        return pid, minor

    def destroy(self, pid: int, minor: int, timeout: int | None = None) -> None:
        super().destroy(pid, minor, timeout)
        self.opened.pop(minor, None)
        self.attached.pop(minor, None)
        if minor in self.minors:
            self.minors.remove(minor)

    def cleanup(self) -> None:
        """Close and detach the minors left, kill the spawned tapdisks, then free the minors."""
        for minor, pid in list(self.opened.items()):
            self._try(self.close, pid, minor)
        for minor, pid in list(self.attached.items()):
            self._try(self.detach, pid, minor)
        # the minors can only be freed once their tapdisk exited
        for pid in self.spawned:
            # the tapdisk may already have exited
            self.host.ssh(f'kill -9 {pid}', check=False)
            wait_for_tapdisk_exit(self.host, pid)
        for minor in list(self.minors):
            self._try(self.free, minor)

    @staticmethod
    def _try(method: Callable[..., None], *args: int) -> None:
        try:
            method(*args)
        except TapCtlError as e:
            logging.warning(f"Cleanup: {e}")

@pytest.fixture
def tapctl(host: Host) -> Generator[TapCtl, None, None]:
    """TapCtl undoing at teardown what the test left behind (open minors, spawned tapdisks...)."""
    tc = TrackingTapCtl(host)
    yield tc
    tc.cleanup()

class TrackingVBDConnector(VBDConnector):
    """VBDConnector remembering what is still connected, to clean it up at teardown."""

    def __init__(self, host: Host, **polling: int):
        super().__init__(host, **polling)
        # (VM uuid, device) -> (VM, "connect" (tapdisk owned) or "attach" (tapdisk not owned))
        self.tracked: dict[tuple[str, str], tuple[VM, str]] = {}

    def connect(self, vm: VM, image: str, device: str, readonly: bool = False,
                log: str | None = None) -> tuple[int, int]:
        # connect() calls attach(), which records "attach" first: override it
        result = super().connect(vm, image, device, readonly, log)
        self.tracked[(vm.uuid, device)] = (vm, "connect")
        return result

    def attach(self, vm: VM, pid: int, minor: int, device: str,
               readonly: bool = True) -> None:
        super().attach(vm, pid, minor, device, readonly)
        self.tracked[(vm.uuid, device)] = (vm, "attach")

    def disconnect(self, vm: VM, device: str) -> None:
        self.tracked.pop((vm.uuid, device), None)
        super().disconnect(vm, device)

    def detach(self, vm: VM, device: str) -> None:
        self.tracked.pop((vm.uuid, device), None)
        super().detach(vm, device)

    def cleanup(self) -> None:
        """Disconnect what the test left connected (destroying the tapdisks it created)."""
        for (_, device), (vm, kind) in list(self.tracked.items()):
            try:
                if kind == "connect":
                    self.disconnect(vm, device)
                else:
                    self.detach(vm, device)
            except Exception as e:
                logging.warning(f"Failed to clean up {device} of VM {vm.uuid}: {e}")

@pytest.fixture
def vbd_connector(host: Host) -> Generator[VBDConnector, None, None]:
    """VBDConnector disconnecting at teardown what the test left connected, with the --blktap polling-* values."""
    polling = {}
    if config.blktap.polling_duration is not None:
        polling['polling_duration_usecs'] = config.blktap.polling_duration
    if config.blktap.polling_idle_threshold is not None:
        polling['polling_idle_threshold'] = config.blktap.polling_idle_threshold
    conn = TrackingVBDConnector(host, **polling)
    yield conn
    conn.cleanup()

@pytest.fixture
def xenstore(host: Host) -> XenStoreHelper:
    return XenStoreHelper(host)

@pytest.fixture
def vm_with_fio(vm_with_vcpu_count: VM) -> VM:
    """The (halted) VM of vm_with_vcpu_count, started, with fio."""
    vm = vm_with_vcpu_count
    vm.start()
    vm.wait_for_vm_running_and_ssh_up()
    package_manager = vm.detect_package_manager()
    match package_manager:
        case PackageManagerEnum.APK:
            vm.ssh('apk add fio')
        case PackageManagerEnum.APT_GET:
            vm.ssh('apt-get update && apt-get install -y fio')
        case PackageManagerEnum.DNF:
            vm.ssh('dnf install -y fio')
        case PackageManagerEnum.YUM:
            vm.ssh('yum install -y fio')
        case PackageManagerEnum.ZYPPER:
            vm.ssh('zypper --non-interactive install fio')
        case PackageManagerEnum.UNKNOWN:
            raise RuntimeError("Unsupported package manager: could not install fio")
        case _:
            assert_never(package_manager)
    return vm
    # no teardown: vm_with_vcpu_count destroys the VM

@pytest.fixture
def connected_vbd(image: str, vbd_connector: VBDConnector, running_vm: VM) -> ConnectedVBD:
    """The image connected to the running VM as xvdb (disconnected by vbd_connector)."""
    # image requested first: fixtures are torn down in reverse order, so the image
    # is removed after vbd_connector disconnected it
    device = "xvdb"
    pid, minor = vbd_connector.connect(running_vm, image, device)
    wait_for_guest_device(running_vm, device)
    return ConnectedVBD(vm=running_vm, device=device, image=image, pid=pid, minor=minor)
