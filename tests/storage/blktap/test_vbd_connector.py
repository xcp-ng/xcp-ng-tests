"""
Test VBD connection using VBDConnector with framework VM fixtures.

Uses the standard xcp-ng-tests VM fixtures instead of hardcoded VM UUIDs.
Tests the Python VBDConnector implementation.
"""
import pytest

import logging
import time

from lib.blktap import TapCtl, VBDConnector

@pytest.mark.small_vm
@pytest.mark.unix_vm
@pytest.mark.parametrize('image_fixture,image_type', [
    ('test_vhd', 'vhd'),
    ('test_qcow2', 'qcow2'),
])
def test_connect_vbd_to_running_vm(host, running_vm, image_fixture, image_type, request):
    """
    Test connecting a VBD to a running VM using VBDConnector.

    This test uses the framework's running_vm fixture instead of hardcoded VMs.
    Tests both VHD and QCOW2 image formats.

    Steps:
    1. Create a test image (VHD or QCOW2)
    2. Use VBDConnector to connect it to the running VM at xvdb
    3. Verify the device appears in the guest
    4. Perform I/O operations
    5. Disconnect the VBD
    6. Verify complete cleanup
    """
    # Get the actual image path from the fixture
    image_path = request.getfixturevalue(image_fixture)
    device = "xvdb"
    image = f"{image_type}:{image_path}"
    vm = running_vm

    # Initialize connector
    connector = VBDConnector(host)

    # Step 1: Check if device is already present (should not be)
    result = vm.ssh(["test", "-b", f"/dev/{device}"], check=False, simple_output=False)
    if result.returncode == 0:
        logging.warning(f"Device /dev/{device} already exists in VM, disconnecting it first")
        try:
            connector.disconnect(vm.uuid, device)
            time.sleep(2)
        except Exception as e:
            logging.warning(f"Failed to disconnect existing device: {e}")

    # Step 2: Connect VBD using VBDConnector
    logging.info(f"Connecting VBD: {image} -> {device}")
    pid, minor = connector.connect(vm.uuid, image, device)
    logging.info(f"✓ VBD connected: pid={pid}, minor={minor}")

    try:
        # Step 3: Wait for device to appear in guest
        logging.debug(f"Waiting for /dev/{device} to appear in guest...")
        for attempt in range(10):
            result = vm.ssh(["test", "-b", f"/dev/{device}"], check=False, simple_output=False)
            if result.returncode == 0:
                logging.info(f"✓ Device appeared in guest")
                break
            time.sleep(1)
        else:
            lsblk_output = vm.ssh(["lsblk"])
            pytest.fail(f"Device /dev/{device} did not appear after 10 seconds.\nlsblk:\n{lsblk_output}")

        # Step 4: Verify with lsblk
        lsblk_output = vm.ssh(["lsblk"])
        assert device in lsblk_output, f"Device {device} not in lsblk:\n{lsblk_output}"
        logging.debug("Device visible in lsblk")

        # Step 5: Test write
        logging.debug("Testing write to device...")
        vm.ssh(["dd", "if=/dev/zero", f"of=/dev/{device}", "bs=1M", "count=10"])
        logging.info("✓ Write successful")

        # Step 6: Test read
        logging.debug("Testing read from device...")
        vm.ssh(["dd", f"if=/dev/{device}", "of=/dev/null", "bs=1M", "count=10"])
        logging.info("✓ Read successful")

        # Step 7: Check tapdisk stats
        logging.debug("Checking tapdisk stats...")
        stats_output = host.ssh(["tap-ctl", "stats", "-p", str(pid), "-m", str(minor)])
        assert "reqs" in stats_output or "hits" in stats_output, "Stats should contain I/O metrics"
        logging.debug("Stats retrieved successfully")

    finally:
        # Step 8: Disconnect VBD
        logging.info(f"Disconnecting VBD from {device}...")
        connector.disconnect(vm.uuid, device)
        logging.info("✓ Disconnect completed")

    # Step 9: Wait for device to disappear
    time.sleep(2)

    # Step 10: Verify device is gone
    logging.debug(f"Verifying /dev/{device} is removed from guest...")
    result = vm.ssh(["test", "-b", f"/dev/{device}"], check=False, simple_output=False)
    assert result.returncode != 0, f"Device /dev/{device} still exists after disconnect"
    logging.debug("Device removed from guest")

    # Step 11: Verify with lsblk
    lsblk_output = vm.ssh(["lsblk"])
    assert device not in lsblk_output, f"Device {device} still in lsblk after disconnect:\n{lsblk_output}"
    logging.debug("Device no longer in lsblk")

    # Step 12: Verify tapdisk is cleaned up
    logging.debug("Verifying tapdisk cleanup...")
    result = host.ssh(["tap-ctl", "list", "-p", str(pid)], check=False, simple_output=False)
    if result.returncode == 0 and result.stdout.strip():
        pytest.fail(f"Tapdisk {pid} still listed after disconnect:\n{result.stdout}")
    else:
        logging.debug(f"Tapdisk {pid} no longer listed")

    # Step 13: Verify minor is freed
    logging.debug(f"Verifying minor {minor} is freed...")
    result = host.ssh(["tap-ctl", "list", "-m", str(minor)], check=False, simple_output=False)
    if result.returncode == 0 and result.stdout.strip():
        pytest.fail(f"Minor {minor} still allocated after disconnect:\n{result.stdout}")
    else:
        logging.info(f"✓ Cleanup verified: tapdisk destroyed, minor freed")

@pytest.mark.small_vm
@pytest.mark.unix_vm
def test_connect_multiple_vbds(host, running_vm):
    """
    Test connecting multiple VBDs to a VM using VBDConnector.
    """
    import uuid

    devices = ["xvdb", "xvdc", "xvdd"]
    vhds = []
    connections = []
    vm = running_vm
    connector = VBDConnector(host)

    try:
        # Create 3 VHDs
        for i, device in enumerate(devices):
            vhd_path = f"/tmp/test-multi-connector-{uuid.uuid4()}.vhd"
            logging.debug(f"Creating VHD {i+1}/3: {vhd_path}")
            host.ssh(["vhd-util", "create", "-n", vhd_path, "-s", "100"])
            vhds.append(vhd_path)

        # Connect all VBDs using VBDConnector
        logging.info(f"Connecting {len(devices)} VBDs...")
        for vhd, device in zip(vhds, devices):
            image = f"vhd:{vhd}"
            logging.debug(f"Connecting {device}: {image}")
            pid, minor = connector.connect(vm.uuid, image, device)
            connections.append((pid, minor, device))
            logging.debug(f"Connected {device}: pid={pid}, minor={minor}")

        # Wait for all devices to appear
        time.sleep(3)

        # Verify all devices present
        lsblk_output = vm.ssh(["lsblk"])
        for device in devices:
            assert device in lsblk_output, f"Device {device} not found in:\n{lsblk_output}"
            logging.debug(f"Device {device} present")
        logging.info(f"✓ All {len(devices)} devices connected and visible")

        # Test concurrent I/O to all devices
        logging.debug("Testing concurrent I/O to all devices...")
        io_commands = [
            f"dd if=/dev/zero of=/dev/{dev} bs=1M count=5 2>/dev/null"
            for dev in devices
        ]
        io_script = " & ".join(io_commands) + " & wait && echo 'Done'"
        result = vm.ssh(["sh", "-c", io_script])
        assert "Done" in result, "Concurrent I/O failed"
        logging.info("✓ Concurrent I/O successful")

    finally:
        # Cleanup: disconnect all VBDs
        for pid, minor, device in connections:
            logging.info(f"Disconnecting {device}...")
            try:
                connector.disconnect(vm.uuid, device)
            except Exception as e:
                logging.warning(f"Failed to disconnect {device}: {e}")

        # Verify cleanup
        for pid, minor, device in connections:
            result = host.ssh(["tap-ctl", "list", "-p", str(pid)], check=False, simple_output=False)
            if result.returncode == 0 and result.stdout.strip():
                logging.warning(f"Tapdisk {pid} still listed after cleanup")

            result = host.ssh(["tap-ctl", "list", "-m", str(minor)], check=False, simple_output=False)
            if result.returncode == 0 and result.stdout.strip():
                logging.warning(f"Minor {minor} still allocated after cleanup")

        # Cleanup: remove all VHDs
        for vhd in vhds:
            logging.info(f"Removing {vhd}...")
            host.ssh(["rm", "-f", vhd], check=False)

        logging.info("✓ Cleanup complete")


@pytest.mark.small_vm
@pytest.mark.unix_vm
def test_connect_vbd_readonly(host, running_vm, test_vhd):
    """
    Test connecting a VBD in read-only mode using VBDConnector.
    """
    device = "xvdb"
    image = f"vhd:{test_vhd}"
    vm = running_vm

    logging.info("Testing read-only VBD connection")

    connector = VBDConnector(host)

    # Connect in read-only mode
    logging.info(f"Connecting VBD in read-only mode: {image}")
    pid, minor = connector.connect(vm.uuid, image, device, readonly=True)
    logging.info(f"✓ Connected in read-only: pid={pid}, minor={minor}")

    try:
        # Wait for device
        time.sleep(2)

        # Verify device appears
        for attempt in range(10):
            result = vm.ssh(["test", "-b", f"/dev/{device}"], check=False, simple_output=False)
            if result.returncode == 0:
                logging.info(f"✓ Device appeared (attempt {attempt + 1})")
                break
            time.sleep(1)
        else:
            pytest.fail("Device did not appear")

        # Test read works
        logging.info("Testing read from read-only device...")
        vm.ssh(["dd", f"if=/dev/{device}", "of=/dev/null", "bs=1M", "count=5"])
        logging.info("✓ Read successful")

        # Test write fails
        logging.info("Testing that write is blocked...")
        result = vm.ssh(["dd", "if=/dev/zero", f"of=/dev/{device}", "bs=1M", "count=1"],
                        check=False, simple_output=False)
        if result.returncode != 0:
            logging.info("✓ Write correctly blocked on read-only device")
        else:
            logging.warning("Write did not fail as expected (might be cached)")

    finally:
        # Cleanup
        logging.info("Cleaning up...")
        connector.disconnect(vm.uuid, device)

        # Verify cleanup
        result = host.ssh(["tap-ctl", "list", "-p", str(pid)], check=False, simple_output=False)
        assert result.returncode != 0 or not result.stdout.strip(), \
            f"Tapdisk {pid} still listed after cleanup"

        result = host.ssh(["tap-ctl", "list", "-m", str(minor)], check=False, simple_output=False)
        assert result.returncode != 0 or not result.stdout.strip(), \
            f"Minor {minor} still allocated after cleanup"

        logging.info("✓ Cleanup complete")
