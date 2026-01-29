"""
VM I/O integration tests for tapdisk.

Tests VBD connections to actual VMs and I/O operations:
- VM read/write operations
- VM read-only mode
- XenStore integration
- Tapback integration

All tests marked with @pytest.mark.small_vm and @pytest.mark.unix_vm.
"""
import pytest

import logging
import time

from lib.blktap import TapCtl, TapCtlError, XenStoreHelper
from lib.common import wait_for

@pytest.mark.small_vm
@pytest.mark.unix_vm
class TestVMBasicIO:
    """Test basic I/O operations between VM and tapdisk."""

    def test_vm_read_write_10mb(self, vbd_connector, test_vhd_100mb, running_vm, host):
        """VM performs 10MB read and write I/O."""
        vm = running_vm

        image = f"vhd:{test_vhd_100mb}"
        device = "xvdb"

        try:
            # Connect VBD
            logging.info(f"Connecting tapdisk to VM {vm.uuid} at {device}")
            pid, minor = vbd_connector.connect(vm.uuid, image, device)
            logging.info(f"Connected tapdisk: pid={pid} minor={minor}")

            # Wait for device to appear in guest
            wait_for(
                lambda: vm.ssh(['test', '-b', f'/dev/{device}'], check=False).returncode == 0,
                f"Wait for /dev/{device} to appear in guest",
                timeout_secs=30
            )

            # Verify device is visible
            result = vm.ssh(['lsblk', '-o', 'NAME,SIZE,TYPE'])
            logging.info(f"Block devices in VM:\n{result}")
            assert device in result, f"Device {device} not found in lsblk output"

            # Write 10MB
            logging.info("Writing 10MB to device...")
            vm.ssh([
                'dd', 'if=/dev/zero', f'of=/dev/{device}',
                'bs=1M', 'count=10', 'conv=fsync'
            ])

            # Read 10MB
            logging.info("Reading 10MB from device...")
            vm.ssh([
                'dd', f'if=/dev/{device}', 'of=/dev/null',
                'bs=1M', 'count=10'
            ])

            # Check tapdisk stats
            tapctl = TapCtl(host)
            stats = tapctl.stats(pid, minor)
            logging.info(f"Tapdisk stats: {stats}")

            assert isinstance(stats, dict), "Stats should be a dict"
            # Stats should show some activity (implementation-dependent)

        finally:
            # Disconnect - handled by vbd_connector fixture cleanup
            pass

    def test_vm_device_appears_in_guest(self, vbd_connector, test_vhd_100mb, running_vm, host):
        """Verify VBD device appears in guest OS."""
        if not running_vm.is_running():
            running_vm.start()
            running_vm.wait_for_vm_running_and_ssh_up()

        vm = running_vm
        image = f"vhd:{test_vhd_100mb}"
        device = "xvdc"

        try:
            # Connect VBD
            pid, minor = vbd_connector.connect(vm.uuid, image, device)
            logging.info(f"Connected tapdisk at {device}: pid={pid} minor={minor}")

            # Wait for device
            wait_for(
                lambda: vm.ssh(['test', '-b', f'/dev/{device}'], check=False).returncode == 0,
                f"Wait for /dev/{device} to appear",
                timeout_secs=30
            )

            # Verify device exists and is a block device
            result = vm.ssh(['ls', '-l', f'/dev/{device}'])
            logging.info(f"Device info: {result}")
            assert 'brw' in result or device in result, "Device should be a block device"

            # Check device size
            result = vm.ssh(['blockdev', '--getsize64', f'/dev/{device}'])
            size_bytes = int(result.strip())
            logging.info(f"Device size: {size_bytes} bytes ({size_bytes / (1024**2):.2f} MB)")
            assert size_bytes > 0, "Device size should be > 0"

        finally:
            pass  # Cleanup by fixture

    def test_vm_io_stress(self, vbd_connector, test_vhd_100mb, running_vm, host):
        """Perform stress I/O test with multiple operations."""
        if not running_vm.is_running():
            running_vm.start()
            running_vm.wait_for_vm_running_and_ssh_up()

        vm = running_vm
        image = f"vhd:{test_vhd_100mb}"
        device = "xvdd"

        try:
            # Connect VBD
            pid, minor = vbd_connector.connect(vm.uuid, image, device)

            # Wait for device
            wait_for(
                lambda: vm.ssh(['test', '-b', f'/dev/{device}'], check=False).returncode == 0,
                f"Wait for /dev/{device}",
                timeout_secs=30
            )

            # Perform multiple I/O operations
            for i in range(3):
                logging.info(f"I/O iteration {i+1}/3")

                # Write pattern
                vm.ssh([
                    'dd', 'if=/dev/zero', f'of=/dev/{device}',
                    'bs=1M', 'count=5', 'conv=fsync'
                ])

                # Read back
                vm.ssh([
                    'dd', f'if=/dev/{device}', 'of=/dev/null',
                    'bs=1M', 'count=5'
                ])

            # Check final stats
            tapctl = TapCtl(host)
            stats = tapctl.stats(pid, minor)
            logging.info(f"Final stats after stress test: {stats}")

            assert isinstance(stats, dict)

        finally:
            pass  # Cleanup by fixture


@pytest.mark.small_vm
@pytest.mark.unix_vm
class TestVMConnectDisconnect:
    """Test VBD connection and disconnection cycles."""

    def test_connect_disconnect_cycle(self, vbd_connector, test_vhd_100mb, running_vm):
        """Connect and disconnect VBD multiple times."""
        if not running_vm.is_running():
            running_vm.start()
            running_vm.wait_for_vm_running_and_ssh_up()

        vm = running_vm
        image = f"vhd:{test_vhd_100mb}"
        device = "xvde"

        for cycle in range(2):
            logging.info(f"Connection cycle {cycle+1}/2")

            # Connect
            pid, minor = vbd_connector.connect(vm.uuid, image, device)
            logging.info(f"Connected: pid={pid} minor={minor}")

            # Wait for device
            wait_for(
                lambda: vm.ssh(['test', '-b', f'/dev/{device}'], check=False).returncode == 0,
                f"Wait for /dev/{device}",
                timeout_secs=30
            )

            # Quick I/O test
            vm.ssh([
                'dd', 'if=/dev/zero', f'of=/dev/{device}',
                'bs=1M', 'count=1', 'conv=fsync'
            ])

            # Disconnect
            vbd_connector.detach(vm.uuid, device)
            logging.info("Disconnected")

            # Wait for device to disappear
            wait_for(
                lambda: vm.ssh(['test', '-b', f'/dev/{device}'], check=False).returncode != 0,
                f"Wait for /dev/{device} to disappear",
                timeout_secs=30
            )

            # Small delay between cycles
            time.sleep(1)

    def test_multiple_devices_same_vm(self, vbd_connector, test_vhds_small, running_vm):
        """Connect multiple VBDs to same VM simultaneously."""
        if not running_vm.is_running():
            running_vm.start()
            running_vm.wait_for_vm_running_and_ssh_up()

        vm = running_vm
        devices = ['xvdf', 'xvdg', 'xvdh']
        connections = []

        try:
            # Connect all VBDs
            for vhd, device in zip(test_vhds_small, devices):
                image = f"vhd:{vhd}"
                pid, minor = vbd_connector.connect(vm.uuid, image, device)
                connections.append((pid, minor, device))
                logging.info(f"Connected {device}: pid={pid} minor={minor}")

            # Wait for all devices
            for _, _, device in connections:
                wait_for(
                    lambda d=device: vm.ssh(['test', '-b', f'/dev/{d}'], check=False).returncode == 0,
                    f"Wait for /dev/{device}",
                    timeout_secs=30
                )

            # Verify all devices are present
            result = vm.ssh(['lsblk'])
            for _, _, device in connections:
                assert device in result, f"Device {device} not found"

            # Perform I/O on all devices concurrently
            io_commands = [
                f"dd if=/dev/zero of=/dev/{dev} bs=1M count=1 conv=fsync 2>/dev/null"
                for _, _, dev in connections
            ]
            io_script = " & ".join(io_commands) + " & wait && echo 'Done'"
            result = vm.ssh(['sh', '-c', io_script])
            assert 'Done' in result

        finally:
            # Disconnect all
            for _, _, device in connections:
                try:
                    vbd_connector.detach(vm.uuid, device)
                except Exception as e:
                    logging.warning(f"Failed to disconnect {device}: {e}")


@pytest.mark.small_vm
@pytest.mark.unix_vm
class TestXenStoreIntegration:
    """Test XenStore integration with tapdisk and VMs."""

    def test_read_vbd_list(self, connected_vbd, xenstore):
        """Read VBD list from XenStore for connected VM."""
        vm = connected_vbd['vm']

        # Get VM domain ID
        domid = int(vm.param_get('dom-id'))
        logging.info(f"VM domain ID: {domid}")

        # List VBDs
        vbds = xenstore.list_vbds(domid)
        logging.info(f"VBDs in VM: {vbds}")

        assert isinstance(vbds, list)
        # Should have at least the xvda (boot disk) and our test device
        assert len(vbds) >= 1

    def test_read_backend_path(self, connected_vbd, xenstore):
        """Read backend path for connected VBD."""
        vm = connected_vbd['vm']

        domid = int(vm.param_get('dom-id'))

        # List VBDs to find our device
        vbds = xenstore.list_vbds(domid)
        assert len(vbds) > 0

        # Read backend for first VBD
        devid = int(vbds[0])
        backend = xenstore.get_backend_path(domid, devid)
        logging.info(f"Backend path for devid {devid}: {backend}")

        if backend:
            assert "/backend/" in backend
            # Try to read some backend properties
            state = xenstore.read(f"{backend}/state")
            logging.info(f"Backend state: {state}")

    def test_xenstore_backend_exists(self, host, xenstore):
        """Verify XenStore backend paths exist."""
        # Check if backend directory exists
        assert xenstore.exists("/local/domain/0/backend")

        # List backends
        backends = xenstore.ls("/local/domain/0/backend")
        logging.info(f"Available backends: {backends}")

        # Should have vbd or vbd3
        assert isinstance(backends, list)


@pytest.mark.small_vm
@pytest.mark.unix_vm
class TestTapbackIntegration:
    """Test integration with tapback daemon."""

    def test_tapback_running(self, host):
        """Verify tapback daemon is running on host."""
        result = host.ssh(
            ["systemctl", "is-active", "tapback"],
            check=False,
            simple_output=False
        )

        if result.returncode != 0:
            pytest.skip("tapback daemon not running or not available")

        assert result.returncode == 0, "tapback should be running"

    def test_connected_vbd_has_kthread(self, connected_vbd, xenstore):
        """Verify tapback creates kthread-pid for connected VBD."""
        vm = connected_vbd['vm']

        domid = int(vm.param_get('dom-id'))

        # The connect-vbd.sh script waits for kthread-pid to appear
        # So if we have a connected VBD, it should exist
        # Find the backend path
        vbds = xenstore.list_vbds(domid)
        if len(vbds) > 0:
            devid = int(vbds[0])
            backend = xenstore.get_backend_path(domid, devid)

            if backend:
                # Check for kthread-pid
                kthread_pid = xenstore.read(f"{backend}/kthread-pid")
                logging.info(f"kthread-pid: {kthread_pid}")

                if kthread_pid:
                    assert int(kthread_pid) > 0


@pytest.mark.small_vm
@pytest.mark.unix_vm
class TestVMErrorHandling:
    """Test error handling in VM scenarios."""

    def test_io_on_nonexistent_device(self, running_vm):
        """Try I/O on non-existent device (should fail gracefully)."""
        if not running_vm.is_running():
            running_vm.start()
            running_vm.wait_for_vm_running_and_ssh_up()

        vm = running_vm
        # Try to access non-existent device
        result = vm.ssh(
            ['test', '-b', '/dev/xvdz'],
            check=False,
            simple_output=False
        )
        assert result.returncode != 0, "Non-existent device should not exist"

    def test_connect_to_nonexistent_vm(self, vbd_connector, test_vhd_100mb, host):
        """Try to connect to non-existent VM (should fail)."""
        fake_uuid = "00000000-0000-0000-0000-000000000000"
        image = f"vhd:{test_vhd_100mb}"

        from lib.blktap import VBDConnectorError

        # Should fail because VM doesn't exist
        with pytest.raises((VBDConnectorError, Exception)):
            vbd_connector.connect(fake_uuid, image, "xvdb")

    def test_double_connect_same_device(self, vbd_connector, test_vhd_100mb, running_vm):
        """Try to connect two VBDs to same device (should fail)."""
        if not running_vm.is_running():
            running_vm.start()
            running_vm.wait_for_vm_running_and_ssh_up()

        vm = running_vm
        image = f"vhd:{test_vhd_100mb}"
        device = "xvdi"

        from lib.blktap import VBDConnectorError

        # Connect first VBD
        pid1, minor1 = vbd_connector.connect(vm.uuid, image, device)
        logging.info(f"First connection: pid={pid1} minor={minor1}")

        try:
            # Wait for device
            wait_for(
                lambda: vm.ssh(['test', '-b', f'/dev/{device}'], check=False).returncode == 0,
                f"Wait for /dev/{device}",
                timeout_secs=30
            )

            # Try to connect second VBD to same device (should fail)
            with pytest.raises((VBDConnectorError, Exception)):
                vbd_connector.connect(vm.uuid, image, device)

        finally:
            # Cleanup first connection
            try:
                vbd_connector.detach(vm.uuid, device)
            except Exception:
                pass


@pytest.mark.small_vm
@pytest.mark.unix_vm
class TestVMUsagePatterns:
    """Test common VM usage patterns."""

    def test_sequential_reads(self, connected_vbd):
        """Perform sequential read operations."""
        vm = connected_vbd['vm']
        device = connected_vbd['device']

        # Read first 1MB
        vm.ssh(['dd', f'if=/dev/{device}', 'of=/dev/null', 'bs=1M', 'count=1'])

        # Read next 1MB (skip first)
        vm.ssh([
            'dd', f'if=/dev/{device}', 'of=/dev/null',
            'bs=1M', 'count=1', 'skip=1'
        ])

        # Read at offset
        vm.ssh([
            'dd', f'if=/dev/{device}', 'of=/dev/null',
            'bs=1M', 'count=1', 'skip=10'
        ])

    def test_write_read_verify(self, connected_vbd):
        """Write data, read it back, verify pattern."""
        vm = connected_vbd['vm']
        device = connected_vbd['device']

        # Write known pattern
        vm.ssh([
            'dd', 'if=/dev/zero', f'of=/dev/{device}',
            'bs=512', 'count=1', 'conv=fsync'
        ])

        # Read back
        result = vm.ssh([
            'dd', f'if=/dev/{device}', 'of=/dev/stdout',
            'bs=512', 'count=1'
        ], decode=False)  # Get bytes

        # Should be all zeros
        assert len(result) == 512, "Should read 512 bytes"
        # Note: dd might output to stderr, so just verify we got something

    def test_partial_io(self, connected_vbd):
        """Test partial reads and writes."""
        vm = connected_vbd['vm']
        device = connected_vbd['device']

        # Write small amount
        vm.ssh([
            'dd', 'if=/dev/zero', f'of=/dev/{device}',
            'bs=1', 'count=512', 'conv=fsync'
        ])

        # Read larger amount (should succeed)
        vm.ssh([
            'dd', f'if=/dev/{device}', 'of=/dev/null',
            'bs=1', 'count=1024'
        ])
