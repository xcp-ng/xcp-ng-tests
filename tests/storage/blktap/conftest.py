from __future__ import annotations

import pytest

import logging
import uuid

from lib.blktap import TapCtl, TapCtlError, VBDConnector, XenStoreHelper
from tests.storage.blktap import ConnectedVBD, wait_for_guest_device, wait_for_tapdisk_exit

from typing import TYPE_CHECKING, Callable, Generator

if TYPE_CHECKING:
    from lib.host import Host
    from lib.vm import VM

def _create_image(host: Host, image_format: str) -> str:
    """Create a 100 MiB image in dom0 /tmp, return its path."""
    path = f"/tmp/blktap-test-{uuid.uuid4()}.{image_format}"
    if image_format == 'vhd':
        host.ssh(f'vhd-util create -n {path} -s 100')
    else:
        host.ssh(f'qemu-img create -f {image_format} {path} 100M')
    return path

@pytest.fixture
def vhd_path(host: Host) -> Generator[str, None, None]:
    path = _create_image(host, 'vhd')
    yield path
    host.ssh(f'rm -f {path}')

@pytest.fixture
def qcow2_path(host: Host) -> Generator[str, None, None]:
    path = _create_image(host, 'qcow2')
    yield path
    host.ssh(f'rm -f {path}')

@pytest.fixture
def vhd_paths(host: Host) -> Generator[list[str], None, None]:
    paths = [_create_image(host, 'vhd') for _ in range(3)]
    yield paths
    host.ssh(f"rm -f {' '.join(paths)}")

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
             timeout: int | None = None) -> None:
        super().open(pid, minor, path, readonly, no_o_direct, timeout)
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

    def create(self, path: str, readonly: bool = False) -> tuple[int, int]:
        pid, minor = super().create(path, readonly)
        self.spawned.append(pid)
        self.minors.append(minor)
        self.attached[minor] = pid
        self.opened[minor] = pid
        return pid, minor

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

    def __init__(self, host: Host):
        super().__init__(host)
        # (VM uuid, device) -> (VM, "connect" (tapdisk owned) or "attach" (tapdisk not owned))
        self.tracked: dict[tuple[str, str], tuple[VM, str]] = {}

    def connect(self, vm: VM, image: str, device: str,
                readonly: bool = False) -> tuple[int, int]:
        # connect() calls attach(), which records "attach" first: override it
        result = super().connect(vm, image, device, readonly)
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
    """VBDConnector disconnecting at teardown what the test left connected."""
    conn = TrackingVBDConnector(host)
    yield conn
    conn.cleanup()

@pytest.fixture
def xenstore(host: Host) -> XenStoreHelper:
    return XenStoreHelper(host)

@pytest.fixture
def connected_vbd(vhd_path: str, vbd_connector: VBDConnector, running_vm: VM) -> ConnectedVBD:
    """A 100 MiB VHD connected to the running VM as xvdb (disconnected by vbd_connector)."""
    # vhd_path requested first: fixtures are torn down in reverse order, so the image
    # is removed after vbd_connector disconnected it
    image = f"vhd:{vhd_path}"
    device = "xvdb"
    pid, minor = vbd_connector.connect(running_vm, image, device)
    wait_for_guest_device(running_vm, device)
    return ConnectedVBD(vm=running_vm, device=device, image=image, pid=pid, minor=minor)
