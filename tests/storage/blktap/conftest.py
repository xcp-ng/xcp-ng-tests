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
