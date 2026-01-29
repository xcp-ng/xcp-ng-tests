"""Fixtures for blktap/tapdisk tests."""
from __future__ import annotations

import pytest

import logging
import uuid
from pathlib import Path

from lib.blktap import TapCtl, VBDConnector, XenStoreHelper
from lib.common import wait_for

from typing import TYPE_CHECKING, Any, Dict, Generator, List, Tuple

if TYPE_CHECKING:
    from lib.host import Host
    from lib.vm import VM


# Note: connect_vbd_script fixture removed - VBDConnector now uses Python implementation


@pytest.fixture(scope='package')
def test_vhd_100mb(host):
    """
    Create a 100MB test VHD on dom0.

    Yields:
        str: Path to VHD file on dom0

    Cleanup:
        Removes the VHD file
    """
    vhd_path = f"/tmp/test-{uuid.uuid4()}.vhd"

    # Create VHD using vhd-util
    host.ssh(["vhd-util", "create", "-n", vhd_path, "-s", "100"])
    logging.info(f"Created test VHD: {host}:{vhd_path}")

    yield vhd_path

    # Cleanup
    try:
        host.ssh(["rm", "-f", vhd_path], check=False)
        logging.info(f"Removed test VHD: {host}:{vhd_path}")
    except Exception as e:
        logging.warning(f"Failed to remove {vhd_path}: {e}")


@pytest.fixture
def test_vhd(host):
    """Default 100MB test VHD (function-scoped)."""
    vhd_path = f"/tmp/test-{uuid.uuid4()}.vhd"
    host.ssh(["vhd-util", "create", "-n", vhd_path, "-s", "100"])
    logging.info(f"Created test VHD: {vhd_path}")

    yield vhd_path

    try:
        host.ssh(["rm", "-f", vhd_path], check=False)
        logging.info(f"Removed test VHD: {vhd_path}")
    except Exception as e:
        logging.warning(f"Failed to remove {vhd_path}: {e}")


@pytest.fixture
def test_vhd_small(host):
    """Alias for test_vhd (100MB VHD)."""
    vhd_path = f"/tmp/test-small-{uuid.uuid4()}.vhd"
    host.ssh(["vhd-util", "create", "-n", vhd_path, "-s", "100"])
    logging.info(f"Created test VHD: {host}:{vhd_path}")

    yield vhd_path

    # Cleanup
    try:
        host.ssh(["rm", "-f", vhd_path], check=False)
        logging.info(f"Removed test VHD: {host}:{vhd_path}")
    except Exception as e:
        logging.warning(f"Failed to remove {vhd_path}: {e}")


@pytest.fixture(scope='package')
def test_qcow2_100mb(host):
    """Package-scoped 100MB test QCOW2."""
    qcow2_path = f"/tmp/test-{uuid.uuid4()}.qcow2"
    host.ssh(["qemu-img", "create", "-f", "qcow2", qcow2_path, "100M"])
    logging.info(f"Created test QCOW2: {host}:{qcow2_path}")

    yield qcow2_path

    try:
        host.ssh(["rm", "-f", qcow2_path], check=False)
        logging.info(f"Removed test QCOW2: {host}:{qcow2_path}")
    except Exception as e:
        logging.warning(f"Failed to remove {qcow2_path}: {e}")


@pytest.fixture
def test_qcow2(host):
    """Default 100MB test QCOW2 (function-scoped)."""
    qcow2_path = f"/tmp/test-{uuid.uuid4()}.qcow2"
    host.ssh(["qemu-img", "create", "-f", "qcow2", qcow2_path, "100M"])
    logging.info(f"Created test QCOW2: {qcow2_path}")

    yield qcow2_path

    try:
        host.ssh(["rm", "-f", qcow2_path], check=False)
        logging.info(f"Removed test QCOW2: {qcow2_path}")
    except Exception as e:
        logging.warning(f"Failed to remove {qcow2_path}: {e}")


@pytest.fixture
def test_qcow2_small(host):
    """Alias for test_qcow2 (100MB QCOW2)."""
    qcow2_path = f"/tmp/test-small-{uuid.uuid4()}.qcow2"
    host.ssh(["qemu-img", "create", "-f", "qcow2", qcow2_path, "100M"])
    logging.info(f"Created test QCOW2: {host}:{qcow2_path}")

    yield qcow2_path

    try:
        host.ssh(["rm", "-f", qcow2_path], check=False)
        logging.info(f"Removed test QCOW2: {host}:{qcow2_path}")
    except Exception as e:
        logging.warning(f"Failed to remove {qcow2_path}: {e}")


@pytest.fixture
def test_vhds_small(host) -> Generator[List[str], None, None]:
    """Create 3x 100MB test VHDs."""
    vhds = []
    for i in range(3):
        vhd = f"/tmp/disk{i}-{uuid.uuid4()}.vhd"
        host.ssh(["vhd-util", "create", "-n", vhd, "-s", "100"])
        vhds.append(vhd)
        logging.info(f"Created test VHD {i+1}/3: {host}:{vhd}")

    yield vhds

    # Cleanup
    for vhd in vhds:
        try:
            host.ssh(["rm", "-f", vhd], check=False)
            logging.info(f"Removed test VHD: {host}:{vhd}")
        except Exception as e:
            logging.warning(f"Failed to remove {vhd}: {e}")


@pytest.fixture
def tapctl(host):
    """TapCtl instance with automatic cleanup of created tapdisks."""
    tc = TapCtl(host)
    created_resources = []

    original_create = tc.create

    def tracked_create(*args, **kwargs):
        pid, minor = original_create(*args, **kwargs)
        created_resources.append((pid, minor))
        logging.info(f"Tracked tapdisk creation: pid={pid} minor={minor}")
        return pid, minor
    tc.create = tracked_create

    yield tc

    # Cleanup all created tapdisks
    for pid, minor in created_resources:
        try:
            tc.destroy(pid, minor)
            logging.info(f"Cleaned up tapdisk: pid={pid} minor={minor}")
        except Exception as e:
            logging.warning(f"Failed to cleanup tapdisk {pid}/{minor}: {e}")


@pytest.fixture
def vbd_connector(host):
    """VBDConnector instance with automatic cleanup of connected VBDs."""
    conn = VBDConnector(host)
    connected_vbds = []

    original_connect = conn.connect

    def tracked_connect(vm_uuid, image, device, readonly=False, max_queues=1):
        result = original_connect(vm_uuid, image, device, readonly, max_queues)
        connected_vbds.append((vm_uuid, device))
        logging.info(f"Tracked VBD connection: vm={vm_uuid} device={device}")
        return result
    conn.connect = tracked_connect

    original_attach = conn.attach

    def tracked_attach(vm_uuid, pid, minor, device, readonly=True, max_queues=1):
        result = original_attach(vm_uuid, pid, minor, device, readonly, max_queues)
        connected_vbds.append((vm_uuid, device))
        logging.info(f"Tracked VBD attach: vm={vm_uuid} device={device}")
        return result
    conn.attach = tracked_attach

    yield conn

    for vm_uuid, device in connected_vbds:
        try:
            conn.detach(vm_uuid, device)
            logging.info(f"Cleaned up VBD connection: vm={vm_uuid} device={device}")
        except Exception as e:
            logging.warning(f"Failed to disconnect {vm_uuid}/{device}: {e}")


@pytest.fixture
def xenstore(host):
    """XenStoreHelper instance."""
    return XenStoreHelper(host)


@pytest.fixture
def connected_vbd(vbd_connector, test_vhd_100mb, running_vm, host) -> Generator[Dict[str, Any], None, None]:
    """VBD connected to running VM at xvdb."""
    vm = running_vm
    image = f"vhd:{test_vhd_100mb}"
    device = "xvdb"

    pid, minor = vbd_connector.connect(vm.uuid, image, device)

    wait_for(
        lambda: vm.ssh(['test', '-b', f'/dev/{device}'], check=False).returncode == 0,
        f"Wait for /dev/{device} to appear in guest",
        timeout_secs=30
    )

    yield {
        'pid': pid,
        'minor': minor,
        'device': device,
        'vm': vm,
        'image': image,
        'vhd_path': test_vhd_100mb
    }
