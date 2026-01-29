"""
Blktap/tapdisk abstraction library for xcp-ng-tests.

Provides wrapper classes for:
- tap-ctl command line tool (via SSH to dom0)
- connect-vbd.sh script operations (via SSH to dom0)
- XenStore operations (via SSH to dom0)

All operations run via SSH to the XCP-ng host.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from lib.host import Host


@dataclass
class TapdiskInfo:
    """Information about a running tapdisk."""
    pid: int
    minor: int
    state: str
    type: str
    path: str


class TapCtlError(Exception):
    """Exception raised when tap-ctl command fails."""
    pass


class TapCtl:
    """
    Wrapper for tap-ctl command line interface.

    Runs tap-ctl commands on dom0 via SSH.
    """

    def __init__(self, host: Host):
        """Initialize TapCtl wrapper."""
        self.host = host

    def _run(self, *args, check=True) -> str:
        """
        Run tap-ctl command on dom0.

        Args:
            *args: Arguments to pass to tap-ctl
            check: Whether to raise exception on error

        Returns:
            Command stdout

        Raises:
            TapCtlError: If command fails and check=True
        """
        cmd = ['tap-ctl'] + list(str(arg) for arg in args)
        try:
            result = self.host.ssh(cmd, check=check, simple_output=True)
            return result
        except Exception as e:
            if check:
                raise TapCtlError(f"tap-ctl {' '.join(str(a) for a in args)} failed: {e}")
            return ""

    def spawn(self) -> int:
        """
        Spawn a new tapdisk daemon.

        Returns:
            The PID of the spawned tapdisk

        Raises:
            TapCtlError: If spawn fails or PID cannot be parsed
        """
        result = self._run("spawn")
        # Output format: "tapdisk spawned with pid 12345" or just "12345"
        output = result.strip()
        match = re.search(r'(\d+)', output)
        if match:
            return int(match.group(1))
        raise TapCtlError(f"Could not parse spawn output: {output}")

    def allocate(self) -> Tuple[int, str]:
        """
        Allocate a device minor.

        Returns:
            Tuple of (minor, device_path)

        Raises:
            TapCtlError: If allocation fails or output cannot be parsed
        """
        result = self._run("allocate")
        # Output format: "/dev/xen/blktap-2/tapdev0" or similar
        device = result.strip()
        # Extract minor from device path (e.g., tapdev0 -> 0)
        match = re.search(r'tapdev(\d+)', device)
        if match:
            minor = int(match.group(1))
            return minor, device
        raise TapCtlError(f"Could not parse allocate output: {device}")

    def free(self, minor: int):
        """Free a device minor."""
        self._run("free", "-m", str(minor))

    def attach(self, pid: int, minor: int):
        """Attach a minor to a tapdisk process."""
        self._run("attach", "-p", str(pid), "-m", str(minor))

    def detach(self, pid: int, minor: int):
        """Detach a minor from a tapdisk process."""
        self._run("detach", "-p", str(pid), "-m", str(minor))

    def open(
        self,
        pid: int,
        minor: int,
        path: str,
        readonly: bool = False,
        no_o_direct: bool = False,
        timeout: Optional[int] = None
    ):
        """
        Open a disk image.

        Args:
            pid: Tapdisk process ID
            minor: Device minor number
            path: Image path in format "type:/path/to/file" (e.g., "vhd:/srv/vhd/disk.vhd")
            readonly: Open in read-only mode
            no_o_direct: Disable O_DIRECT
            timeout: Request timeout in seconds
        """
        args = ["open", "-p", str(pid), "-m", str(minor), "-a", path]
        if readonly:
            args.append("-R")
        if no_o_direct:
            args.append("-D")
        if timeout is not None:
            args.extend(["-t", str(timeout)])
        self._run(*args)

    def close(self, pid: int, minor: int, force: bool = False, timeout: Optional[int] = None):
        """Close a disk image."""
        args = ["close", "-p", str(pid), "-m", str(minor)]
        if force:
            args.append("-f")
        if timeout is not None:
            args.extend(["-t", str(timeout)])
        self._run(*args)

    def pause(self, pid: int, minor: int, timeout: int = 10):
        """Pause VBD I/O."""
        self._run("pause", "-p", str(pid), "-m", str(minor), "-t", str(timeout))

    def unpause(self, pid: int, minor: int, path: Optional[str] = None):
        """Resume VBD I/O, optionally with a new image."""
        args = ["unpause", "-p", str(pid), "-m", str(minor)]
        if path:
            args.extend(["-a", path])
        self._run(*args)

    def list(self, pid: Optional[int] = None, minor: Optional[int] = None) -> List[TapdiskInfo]:
        """
        List running tapdisks.

        Args:
            pid: Filter by process ID (optional)
            minor: Filter by minor number (optional)

        Returns:
            List of TapdiskInfo objects
        """
        args = ["list"]
        if pid is not None:
            args.extend(["-p", str(pid)])
        if minor is not None:
            args.extend(["-m", str(minor)])

        result = self._run(*args)
        tapdisks = []

        for line in result.strip().split('\n'):
            if not line:
                continue
            # Skip column headers (e.g., "pid    minor    state...")
            # but not key=value lines (e.g., "pid=123...")
            if line.startswith('pid') and '=' not in line:
                continue

            # tap-ctl list has two output formats:
            # 1. Columnar (no filters): "PID MINOR STATE TYPE PATH"
            # 2. Key-value (with -p/-m filters): "pid=123 minor=0 state=0 args=vhd:/path"

            if '=' in line:
                # Parse key-value format
                parts_dict = {}
                for part in line.split():
                    if '=' in part:
                        key, value = part.split('=', 1)
                        parts_dict[key] = value

                if 'pid' in parts_dict:
                    # Extract type and path from args (format: "type:/path")
                    args_str = parts_dict.get('args', '')
                    if ':' in args_str:
                        img_type, img_path = args_str.split(':', 1)
                    else:
                        img_type, img_path = '', args_str

                    tapdisks.append(TapdiskInfo(
                        pid=int(parts_dict['pid']),
                        minor=int(parts_dict.get('minor', -1)),
                        state=parts_dict.get('state', '-'),
                        type=img_type,
                        path=img_path
                    ))
            else:
                # Parse columnar format: "PID MINOR STATE TYPE PATH"
                parts = line.split(None, 4)
                if len(parts) >= 4:
                    # Skip lines where minor is "-" (no VBD attached)
                    if parts[1] == '-':
                        continue

                    tapdisks.append(TapdiskInfo(
                        pid=int(parts[0]),
                        minor=int(parts[1]),
                        state=parts[2],
                        type=parts[3],
                        path=parts[4] if len(parts) > 4 else ""
                    ))

        return tapdisks

    def stats(self, pid: int, minor: int) -> Dict:
        """
        Get statistics for a VBD.

        Args:
            pid: Tapdisk process ID
            minor: Device minor number

        Returns:
            Dictionary with statistics

        Raises:
            TapCtlError: If stats cannot be retrieved or parsed
        """
        result = self._run("stats", "-p", str(pid), "-m", str(minor))
        # The output should be Python dict format, try to parse it
        try:
            # Use json to parse if it's JSON format
            return json.loads(result)
        except json.JSONDecodeError:
            # Try to eval as Python dict (less safe but tap-ctl outputs Python format)
            try:
                # Use ast.literal_eval for safer evaluation
                import ast
                return ast.literal_eval(result)
            except Exception:
                raise TapCtlError(f"Could not parse stats output: {result}")

    def create(self, path: str, readonly: bool = False) -> Tuple[int, int]:
        """
        Create a complete tapdisk (spawn + allocate + attach + open).

        Args:
            path: Image path in format "type:/path/to/file"
            readonly: Open in read-only mode

        Returns:
            Tuple of (pid, minor)

        Raises:
            TapCtlError: If creation fails or PID/minor cannot be determined
        """
        args = ["create", "-a", path]
        if readonly:
            args.append("-R")

        result = self._run(*args)
        # Parse output to get pid and minor
        # Format varies, try to extract both
        pid = minor = None
        for line in result.split('\n'):
            if 'pid' in line.lower():
                match = re.search(r'(\d+)', line)
                if match:
                    pid = int(match.group(1))
            if 'minor' in line.lower() or 'tapdev' in line:
                match = re.search(r'(\d+)', line)
                if match:
                    minor = int(match.group(1))

        if pid is None or minor is None:
            # Fallback: list tapdisks and find the newest
            tapdisks = self.list()
            if tapdisks:
                newest = tapdisks[-1]
                return newest.pid, newest.minor
            raise TapCtlError("Could not determine PID/minor from create output")

        return pid, minor

    def destroy(self, pid: int, minor: int, timeout: Optional[int] = None):
        """Destroy a tapdisk (close + detach + free)."""
        args = ["destroy", "-p", str(pid), "-m", str(minor)]
        if timeout is not None:
            args.extend(["-t", str(timeout)])
        self._run(*args)


class VBDConnectorError(Exception):
    """Exception raised when VBD connection fails."""
    pass


class VBDConnector:
    """
    Helper to connect VBDs to VMs via XenStore.

    Implements VBD connection logic directly in Python instead of using shell scripts.
    """

    def __init__(self, host: Host):
        """Initialize VBDConnector."""
        self.host = host
        self.tapctl = TapCtl(host)
        self.xenstore = XenStoreHelper(host)

    def calc_devid(self, device: str) -> int:
        """Calculate device ID from device name (e.g., xvdb -> 51728)."""
        letter = device.replace('xvd', '')
        letter_num = ord(letter) - ord('a')
        return (202 << 8) + (letter_num * 16)

    def _wait_xenstore_key(self, path: str, timeout: int = 10) -> bool:
        """Wait for a XenStore key to exist."""
        for _ in range(timeout * 2):  # Check every 0.5 seconds
            if self.xenstore.exists(path):
                return True
            time.sleep(0.5)
        return False

    def _wait_xenstore_state(self, path: str, min_state: int, timeout: int = 10) -> bool:
        """Wait for XenStore state to reach minimum value."""
        for _ in range(timeout * 2):  # Check every 0.5 seconds
            value = self.xenstore.read(path)
            if value and int(value) >= min_state:
                return True
            time.sleep(0.5)
        return False

    def _write_frontend_entries(self, domid: int, devid: int, frontend: str, backend: str):
        """Write frontend XenStore entries."""
        self.xenstore.write(f"{frontend}/backend", backend)
        self.xenstore.write(f"{frontend}/backend-id", "0")
        self.xenstore.write(f"{frontend}/virtual-device", str(devid))
        self.xenstore.write(f"{frontend}/device-type", "disk")
        self.xenstore.write(f"{frontend}/state", "0")  # State 0 initially
        # Set permissions (tapback needs to read this)
        self.host.ssh(['xenstore-chmod', '-r', frontend, f'b{domid}', 'b0'])

    def _write_backend_entries(self, domid: int, backend: str, frontend: str,
                               device: str, phys: str, mode: str, max_queues: int):
        """Write backend XenStore entries."""
        self.xenstore.write(f"{backend}/frontend", frontend)
        self.xenstore.write(f"{backend}/frontend-id", str(domid))
        self.xenstore.write(f"{backend}/online", "1")
        self.xenstore.write(f"{backend}/removable", "0")
        self.xenstore.write(f"{backend}/dev", device)
        self.xenstore.write(f"{backend}/mode", mode)
        self.xenstore.write(f"{backend}/device-type", "disk")
        self.xenstore.write(f"{backend}/max-ring-page-order", "3")
        if max_queues > 1:
            self.xenstore.write(f"{backend}/multi-queue-max-queues", str(max_queues))
        # Writing physical-device triggers tapback
        self.xenstore.write(f"{backend}/physical-device", phys)
        # Set permissions
        self.host.ssh(['xenstore-chmod', '-r', backend, 'b0', f'r{domid}'])

    def _complete_xenbus_connection(self, backend: str, frontend: str):
        """
        Complete XenBus connection state machine.

        Handles the full state transition sequence:
        1. Wait for tapback to probe tapdisk
        2. Set hotplug-status
        3. Initiate state transitions
        4. Wait for connection to complete (state 4)
        """
        # Step 1: Wait for tapback to probe tapdisk and write kthread-pid
        if not self._wait_xenstore_key(f"{backend}/kthread-pid", timeout=10):
            raise VBDConnectorError("Tapback timeout: kthread-pid not created")

        # Step 2: Set hotplug-status (now that tapback has found frontend_path)
        self.xenstore.write(f"{backend}/hotplug-status", "connected")

        # Step 3: Initiate XenBus handshake by setting states
        # Set frontend state=1 first (guest waits for backend>=2)
        self.xenstore.write(f"{frontend}/state", "1")  # Initialising

        # Set backend state=1 ONLY if tapback hasn't already advanced it
        # (SSH latency can cause tapback to advance before we get here)
        current_state = self.xenstore.read(f"{backend}/state")
        if current_state and int(current_state) > 1:
            logging.info(f"Backend already advanced to state {current_state}, skipping state=1 write")
        else:
            self.xenstore.write(f"{backend}/state", "1")   # Initialising

        # Step 4: Wait for backend to advance to state >= 2
        # Tapback advances when it sees frontend=1 + hotplug_status_connected
        if not self._wait_xenstore_state(f"{backend}/state", 2, timeout=10):
            final_state = self.xenstore.read(f"{backend}/state")
            logging.error(f"Backend did not advance past state 1 (final state: {final_state})")
            tapback_status = self.host.ssh(['systemctl', 'is-active', 'tapback'], check=False)
            logging.error(f"Tapback status: {tapback_status}")
            raise VBDConnectorError("Backend did not advance past state 1")

        # Step 5: Wait for guest (blkfront) to setup rings (state >= 3)
        # Guest sees backend state 2, creates rings, advances to state 3
        if not self._wait_xenstore_state(f"{frontend}/state", 3, timeout=15):
            raise VBDConnectorError("Frontend did not setup rings")

        # Step 6: Wait for tapback to connect (state 4)
        # Tapback reads rings and advances to Connected
        if not self._wait_xenstore_state(f"{backend}/state", 4, timeout=15):
            raise VBDConnectorError("Backend did not reach Connected state")

        # Step 7: Wait for guest to finish connection (state 4)
        if not self._wait_xenstore_state(f"{frontend}/state", 4, timeout=15):
            raise VBDConnectorError("Frontend did not reach Connected state")

    def connect(self, vm_uuid: str, image: str, device: str,
                readonly: bool = False, max_queues: int = 1) -> Tuple[int, int]:
        """
        Connect a VBD to a VM.

        Creates a tapdisk and connects it to the VM via XenStore.

        Args:
            vm_uuid: UUID of the VM
            image: Image path in format "type:/path" (e.g., "vhd:/tmp/test.vhd")
            device: Device name (xvdb, xvdc, etc.)
            readonly: Open in read-only mode
            max_queues: Maximum number of queues (default: 1)

        Returns:
            Tuple of (pid, minor) for the created tapdisk

        Raises:
            VBDConnectorError: If connection fails
        """
        # Create tapdisk
        logging.info(f"Connecting {device}: {image} (readonly={readonly})")
        pid, minor = self.tapctl.create(image, readonly=readonly)

        try:
            # Attach tapdisk to VM
            self.attach(vm_uuid, pid, minor, device, readonly=readonly, max_queues=max_queues)
            logging.info(f"Successfully connected {device}")
            return pid, minor

        except Exception as e:
            # Cleanup on failure
            logging.error(f"Connection failed, cleaning up tapdisk: {e}")
            try:
                self.tapctl.destroy(pid, minor)
            except Exception:
                pass
            raise VBDConnectorError(f"Failed to connect VBD: {e}")

    def disconnect(self, vm_uuid: str, device: str):
        """
        Disconnect a VBD from a VM and destroy the tapdisk.

        Uses manual close + detach + free sequence instead of tap-ctl destroy
        for better control and error handling.

        Args:
            vm_uuid: UUID of the VM
            device: Device name (xvdb, xvdc, etc.)

        Raises:
            VBDConnectorError: If disconnection fails
        """
        # Get VM domain ID
        domid_str = self.host.xe('vm-param-get', {
            'uuid': vm_uuid,
            'param-name': 'dom-id'
        }, check=False)

        if not domid_str or domid_str.strip() == '':
            # VM not running, try to cleanup anyway
            domid = None
        else:
            try:
                domid = int(domid_str.strip())
            except ValueError:
                domid = None

        if domid is None:
            logging.warning(f"VM {vm_uuid} not running, cannot disconnect cleanly")
            return

        # Calculate device ID
        devid = self.calc_devid(device)

        # XenStore paths
        backend = f"/local/domain/0/backend/vbd3/{domid}/{devid}"
        frontend = f"/local/domain/{domid}/device/vbd/{devid}"

        # Check if backend exists
        if not self.xenstore.exists(backend):
            logging.warning(f"Device {device} not connected to VM")
            return

        # Get physical device to find tapdisk
        phys = self.xenstore.read(f"{backend}/physical-device")
        pid = minor = None

        if phys:
            # Extract minor from physical-device (format: "hex_major:hex_minor")
            try:
                _, minor_hex = phys.split(':')
                minor = int(minor_hex, 16)

                # Find tapdisk PID
                tapdisks = self.tapctl.list(minor=minor)
                if tapdisks:
                    pid = tapdisks[0].pid
            except Exception as e:
                logging.warning(f"Could not determine tapdisk info: {e}")

        # Signal closing state to backend (tapback will handle state machine)
        try:
            self.xenstore.write(f"{backend}/state", "5")  # Closing
        except Exception:
            pass

        # Wait for both backend and frontend to reach Closed state (6)
        # This allows tapback and blkfront to cleanly tear down
        for _ in range(20):  # 10 seconds total (0.5s each)
            backend_state = self.xenstore.read(f"{backend}/state")
            frontend_state = self.xenstore.read(f"{frontend}/state")

            if backend_state == "6" and frontend_state == "6":
                break

            time.sleep(0.5)

        # Give tapback a bit more time to finish cleanup
        time.sleep(0.3)

        # Remove XenStore entries
        self.xenstore.rm(backend)
        self.xenstore.rm(frontend)

        # Destroy tapdisk manually (close + detach + free) if we found it
        # XXX: tap-ctl destroy tend to be too quick and free might fails
        #      so call each steps manually
        if pid and minor is not None:
            try:
                logging.info(f"Destroying tapdisk: pid={pid} minor={minor}")
                self.tapctl.close(pid, minor)
                self.tapctl.detach(pid, minor)
                self.tapctl.free(minor)
            except Exception as e:
                logging.warning(f"Failed to destroy tapdisk: {e}")

        logging.info(f"Disconnected {device}")

    def attach(self, vm_uuid: str, pid: int, minor: int, device: str, readonly: bool = True, max_queues: int = 1):
        """
        Attach an existing tapdisk to a VM.

        Connects an already-opened tapdisk to a VM via XenStore.
        Does not create the tapdisk - assumes it's already running.

        Args:
            vm_uuid: UUID of the VM
            pid: Tapdisk process ID
            minor: Device minor number
            device: Device name (xvdb, xvdc, etc.)
            readonly: Attach in read-only mode (default: True for shared attach)
            max_queues: Maximum number of queues (default: 1)

        Raises:
            VBDConnectorError: If attach fails
        """
        # Get VM domain ID
        domid_str = self.host.xe('vm-param-get', {
            'uuid': vm_uuid,
            'param-name': 'dom-id'
        })

        if not domid_str or domid_str.strip() == '':
            raise VBDConnectorError(f"VM {vm_uuid} not running")

        domid = int(domid_str.strip())
        devid = self.calc_devid(device)

        # XenStore paths
        backend = f"/local/domain/0/backend/vbd3/{domid}/{devid}"
        frontend = f"/local/domain/{domid}/device/vbd/{devid}"

        # Clean up any existing entries
        self.xenstore.rm(backend)
        self.xenstore.rm(frontend)

        # Get tapdev path and physical device ID
        tapdev = f"/dev/xen/blktap-2/tapdev{minor}"

        # Verify tapdev exists
        result = self.host.ssh(['test', '-e', tapdev], check=False, simple_output=False)
        if result.returncode != 0:
            raise VBDConnectorError(f"Tapdisk device {tapdev} not found")

        # Get physical device major:minor
        stat_result = self.host.ssh(['stat', '-L', '--format=%t:%T', tapdev])
        phys = stat_result.strip()

        # Write XenStore entries
        mode = "r" if readonly else "w"
        self._write_frontend_entries(domid, devid, frontend, backend)
        self._write_backend_entries(domid, backend, frontend, device, phys, mode, max_queues)

        # Complete XenBus connection state machine
        self._complete_xenbus_connection(backend, frontend)

        logging.info(f"Attached {device} (devid={devid}, readonly={readonly})")

    def detach(self, vm_uuid: str, device: str):
        """
        Detach a VBD from a VM (without destroying the tapdisk).

        Args:
            vm_uuid: UUID of the VM
            device: Device name (xvdb, xvdc, etc.)

        Raises:
            VBDConnectorError: If detach fails
        """
        # Get VM domain ID
        domid_str = self.host.xe('vm-param-get', {
            'uuid': vm_uuid,
            'param-name': 'dom-id'
        }, check=False)

        if not domid_str or domid_str.strip() == '':
            logging.warning(f"VM {vm_uuid} not running")
            return

        domid = int(domid_str.strip())
        devid = self.calc_devid(device)

        # XenStore paths
        backend = f"/local/domain/0/backend/vbd3/{domid}/{devid}"
        frontend = f"/local/domain/{domid}/device/vbd/{devid}"

        # Check if backend exists
        if not self.xenstore.exists(backend):
            logging.warning(f"Device {device} not connected to VM")
            return

        # Signal closing state
        try:
            self.xenstore.write(f"{backend}/state", "5")
        except Exception:
            pass

        # Wait for clean shutdown
        for _ in range(20):
            backend_state = self.xenstore.read(f"{backend}/state")
            frontend_state = self.xenstore.read(f"{frontend}/state")

            if backend_state == "6" and frontend_state == "6":
                break

            time.sleep(0.5)

        # Give tapback time to cleanup
        time.sleep(0.3)

        # Remove XenStore entries (but don't destroy tapdisk)
        self.xenstore.rm(backend)
        self.xenstore.rm(frontend)

        logging.info(f"Detached {device} (tapdisk still running)")

    def status(self, vm_uuid: str, device: str) -> str:
        """
        Get status of a VBD connection.

        Args:
            vm_uuid: UUID of the VM
            device: Device name (xvdb, xvdc, etc.)

        Returns:
            Status output as string
        """
        # Get VM domain ID
        domid_str = self.host.xe('vm-param-get', {
            'uuid': vm_uuid,
            'param-name': 'dom-id'
        }, check=False)

        if not domid_str or domid_str.strip() == '':
            return f"VM {vm_uuid} not running"

        domid = int(domid_str.strip())
        devid = self.calc_devid(device)

        # XenStore paths
        backend = f"/local/domain/0/backend/vbd3/{domid}/{devid}"
        frontend = f"/local/domain/{domid}/device/vbd/{devid}"

        status_lines = []
        status_lines.append(f"Device: {device} (devid={devid})")
        status_lines.append(f"Backend: {backend}")

        if self.xenstore.exists(backend):
            state = self.xenstore.read(f"{backend}/state")
            mode = self.xenstore.read(f"{backend}/mode")
            phys = self.xenstore.read(f"{backend}/physical-device")
            status_lines.append(f"  State: {state}")
            status_lines.append(f"  Mode: {mode}")
            status_lines.append(f"  Physical device: {phys}")
        else:
            status_lines.append("  (not connected)")

        status_lines.append(f"Frontend: {frontend}")
        if self.xenstore.exists(frontend):
            state = self.xenstore.read(f"{frontend}/state")
            status_lines.append(f"  State: {state}")
        else:
            status_lines.append("  (not connected)")

        return "\n".join(status_lines)


class XenStoreError(Exception):
    """Exception raised when XenStore operation fails."""
    pass


class XenStoreHelper:
    """
    Wrapper for XenStore operations using xenstore-* commands.

    Runs xenstore commands on dom0 via SSH.
    """

    def __init__(self, host: Host):
        """
        Initialize XenStoreHelper.

        Args:
            host: XCP-ng host to run commands on
        """
        self.host = host

    def _run(self, cmd: str, *args, check=True) -> str:
        """
        Run xenstore command on dom0.

        Args:
            cmd: xenstore command (e.g., "xenstore-read")
            *args: Arguments to pass to command
            check: Whether to raise exception on error

        Returns:
            Command stdout

        Raises:
            XenStoreError: If command fails and check=True
        """
        try:
            result = self.host.ssh([cmd] + list(str(arg) for arg in args), check=check, simple_output=True)
            return result
        except Exception as e:
            if check:
                raise XenStoreError(f"{cmd} {' '.join(str(a) for a in args)} failed: {e}")
            return ""

    def read(self, path: str) -> Optional[str]:
        """Read a value from XenStore."""
        result = self._run("xenstore-read", path, check=False)
        if not result:
            return None
        return result.strip()

    def write(self, path: str, value: str):
        """Write a value to XenStore."""
        self._run("xenstore-write", path, value)

    def rm(self, path: str):
        """Remove a path from XenStore."""
        self._run("xenstore-rm", path, check=False)

    def exists(self, path: str) -> bool:
        """Check if a XenStore path exists."""
        try:
            self._run("xenstore-exists", path, check=True)
            return True
        except XenStoreError:
            return False

    def ls(self, path: str) -> List[str]:
        """List children of a XenStore path."""
        result = self._run("xenstore-ls", path, check=False)
        if not result:
            return []

        # Parse xenstore-ls output
        children = []
        for line in result.split('\n'):
            line = line.strip()
            if not line:
                continue
            # Extract just the key name (before '=')
            key = line.split('=')[0].strip()
            children.append(key)
        return children

    def list_vbds(self, domid: int) -> List[str]:
        """
        List VBD device IDs for a domain.

        Args:
            domid: Domain ID

        Returns:
            List of device IDs
        """
        path = f"/local/domain/{domid}/device/vbd"
        return self.ls(path)

    def get_backend_path(self, domid: int, devid: int) -> Optional[str]:
        """
        Get backend path for a VBD.

        Args:
            domid: Domain ID
            devid: Device ID

        Returns:
            Backend path, or None if not found
        """
        frontend_path = f"/local/domain/{domid}/device/vbd/{devid}/backend"
        return self.read(frontend_path)
