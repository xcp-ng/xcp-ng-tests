from __future__ import annotations

import pytest

import logging
import uuid

from lib.blktap import VBDConnector

from typing import TYPE_CHECKING, Generator

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
