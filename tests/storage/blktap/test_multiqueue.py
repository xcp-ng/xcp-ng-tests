"""
Test multi-queue VBD connections.

Equivalent to /home/manu/SRC/vates/blktap-xcpng/tools/test-simple-mq.sh

Tests that VBDs can be attached with multi-queue enabled and verifies
the multi-queue-max-queues XenStore setting.
"""
import pytest

import logging
import time

from lib.blktap import VBDConnector, XenStoreHelper

@pytest.mark.small_vm
@pytest.mark.unix_vm
def test_multiqueue_vbd(host, running_vm, test_vhd):
    """
    Test VBD connection with multi-queue enabled.

    Attaches a VHD with max_queues=4 and verifies the multi-queue
    setting is correctly written to XenStore.
    """
    device = "xvdb"
    image = f"vhd:{test_vhd}"
    vm = running_vm
    max_queues = 4

    connector = VBDConnector(host)
    xenstore = XenStoreHelper(host)

    # Connect VBD with multi-queue enabled
    logging.info(f"Connecting VBD with max_queues={max_queues}...")
    pid, minor = connector.connect(vm.uuid, image, device, readonly=False, max_queues=max_queues)
    logging.info(f"✓ VBD connected: pid={pid}, minor={minor}")

    try:
        # Wait for device to appear in guest
        logging.info(f"Waiting for /dev/{device} to appear...")
        for attempt in range(10):
            result = vm.ssh(["test", "-b", f"/dev/{device}"], check=False, simple_output=False)
            if result.returncode == 0:
                logging.info(f"✓ Device appeared (attempt {attempt + 1})")
                break
            time.sleep(1)
        else:
            lsblk_output = vm.ssh(["lsblk"])
            pytest.fail(f"Device /dev/{device} did not appear after 10 seconds.\nlsblk:\n{lsblk_output}")

        # Verify device in lsblk
        lsblk_output = vm.ssh(["lsblk"])
        assert device in lsblk_output, f"Device {device} not in lsblk:\n{lsblk_output}"
        logging.info("✓ Device visible in lsblk")

        # Check multi-queue setting in XenStore
        domid_str = host.xe('vm-param-get', {'uuid': vm.uuid, 'param-name': 'dom-id'})
        domid = int(domid_str.strip())
        devid = connector.calc_devid(device)
        backend = f"/local/domain/0/backend/vbd3/{domid}/{devid}"

        logging.info("Checking multi-queue setting in XenStore...")
        mq_value = xenstore.read(f"{backend}/multi-queue-max-queues")
        logging.info(f"Multi-queue value: {mq_value}")

        assert mq_value is not None, "multi-queue-max-queues not set in XenStore"
        assert int(mq_value) == max_queues, f"Expected max_queues={max_queues}, got {mq_value}"
        logging.info(f"✓ Multi-queue correctly set to {max_queues}")

        # Test I/O works with multi-queue
        logging.info("Testing I/O with multi-queue enabled...")
        vm.ssh(["dd", "if=/dev/zero", f"of=/dev/{device}", "bs=1M", "count=10"])
        logging.info("✓ Write successful")

        vm.ssh(["dd", f"if=/dev/{device}", "of=/dev/null", "bs=1M", "count=10"])
        logging.info("✓ Read successful")

    finally:
        # Cleanup
        logging.info(f"Disconnecting {device}...")
        connector.disconnect(vm.uuid, device)
        logging.info("✓ Cleanup complete")

    # Verify cleanup
    result = host.ssh(["tap-ctl", "list", "-p", str(pid)], check=False, simple_output=False)
    assert result.returncode != 0 or not result.stdout.strip(), \
        f"Tapdisk {pid} still listed after cleanup"

    result = host.ssh(["tap-ctl", "list", "-m", str(minor)], check=False, simple_output=False)
    assert result.returncode != 0 or not result.stdout.strip(), \
        f"Minor {minor} still allocated after cleanup"

    logging.info("=" * 60)
    logging.info("✓ Multi-queue test PASSED!")
    logging.info("=" * 60)


@pytest.mark.small_vm
@pytest.mark.unix_vm
def test_multiqueue_vs_single_queue(host, running_vm, test_vhd):
    """
    Compare single-queue vs multi-queue settings.

    Connects two different devices, one with single queue and one with
    multi-queue, and verifies both work correctly with different settings.
    """
    vm = running_vm
    image = f"vhd:{test_vhd}"
    device_sq = "xvdc"  # Single queue
    device_mq = "xvdd"  # Multi queue

    connector = VBDConnector(host)
    xenstore = XenStoreHelper(host)

    logging.info("Testing single-queue vs multi-queue...")

    # Connect single-queue device
    logging.info(f"Connecting {device_sq} with max_queues=1...")
    pid_sq, minor_sq = connector.connect(vm.uuid, image, device_sq, max_queues=1)

    # Connect multi-queue device
    logging.info(f"Connecting {device_mq} with max_queues=4...")
    pid_mq, minor_mq = connector.connect(vm.uuid, image, device_mq, max_queues=4)

    try:
        # Wait for both devices
        time.sleep(3)

        # Verify both devices present
        lsblk_output = vm.ssh(["lsblk"])
        assert device_sq in lsblk_output, f"Device {device_sq} not found"
        assert device_mq in lsblk_output, f"Device {device_mq} not found"
        logging.info("✓ Both devices present")

        # Check XenStore settings
        domid_str = host.xe('vm-param-get', {'uuid': vm.uuid, 'param-name': 'dom-id'})
        domid = int(domid_str.strip())

        devid_sq = connector.calc_devid(device_sq)
        devid_mq = connector.calc_devid(device_mq)

        backend_sq = f"/local/domain/0/backend/vbd3/{domid}/{devid_sq}"
        backend_mq = f"/local/domain/0/backend/vbd3/{domid}/{devid_mq}"

        mq_value_sq = xenstore.read(f"{backend_sq}/multi-queue-max-queues")
        mq_value_mq = xenstore.read(f"{backend_mq}/multi-queue-max-queues")

        logging.info(f"Single-queue device ({device_sq}): multi-queue-max-queues = {mq_value_sq}")
        logging.info(f"Multi-queue device ({device_mq}): multi-queue-max-queues = {mq_value_mq}")

        assert mq_value_sq == "1", f"Expected 1, got {mq_value_sq}"
        assert mq_value_mq == "4", f"Expected 4, got {mq_value_mq}"
        logging.info("✓ Multi-queue settings correct for both devices")

        # Test I/O on both devices
        logging.info("Testing I/O on both devices...")
        vm.ssh(["dd", "if=/dev/zero", f"of=/dev/{device_sq}", "bs=1M", "count=5"])
        vm.ssh(["dd", "if=/dev/zero", f"of=/dev/{device_mq}", "bs=1M", "count=5"])
        logging.info("✓ I/O successful on both devices")

    finally:
        # Cleanup both devices
        logging.info("Cleaning up...")
        try:
            connector.disconnect(vm.uuid, device_sq)
        except Exception as e:
            logging.warning(f"Failed to disconnect {device_sq}: {e}")

        try:
            connector.disconnect(vm.uuid, device_mq)
        except Exception as e:
            logging.warning(f"Failed to disconnect {device_mq}: {e}")

        logging.info("✓ Cleanup complete")

    logging.info("✓ Single-queue vs multi-queue test PASSED!")
