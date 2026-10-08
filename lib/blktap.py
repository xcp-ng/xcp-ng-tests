"""
Helpers to drive blktap from dom0, over SSH: tapdisks with tap-ctl, VBD connections to
running VMs (equivalent to connect-vbd.sh from blktap-tools) and XenStore.
"""
from __future__ import annotations

import ast
import json
import logging
import re
import shlex
import time
from dataclasses import dataclass

from lib.common import wait_for

from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from lib.host import Host
    from lib.vm import VM

@dataclass
class TapdiskInfo:
    pid: int
    minor: int
    state: str
    type: str
    path: str

class TapCtlError(Exception):
    pass

class TapCtl:
    """Wrapper for the tap-ctl commands."""

    def __init__(self, host: Host) -> None:
        self.host = host

    def _run(self, *args: object, check: bool = True) -> str:
        cmd = shlex.join(['tap-ctl'] + [str(arg) for arg in args])
        try:
            return self.host.ssh(cmd, check=check, simple_output=True)
        except Exception as e:
            if check:
                raise TapCtlError(f"tap-ctl {' '.join(str(a) for a in args)} failed: {e}")
            return ""

    def spawn(self) -> int:
        """Spawn a new tapdisk daemon, return its pid."""
        # Output format: "tapdisk spawned with pid 12345" or just "12345"
        output = self._run("spawn").strip()
        match = re.search(r'(\d+)', output)
        if match:
            return int(match.group(1))
        raise TapCtlError(f"Could not parse spawn output: {output}")

    def allocate(self) -> tuple[int, str]:
        """Allocate a minor, return it with its device path."""
        device = self._run("allocate").strip()
        match = re.search(r'tapdev(\d+)', device)
        if match:
            return int(match.group(1)), device
        raise TapCtlError(f"Could not parse allocate output: {device}")

    def free(self, minor: int) -> None:
        self._run("free", "-m", minor)

    def attach(self, pid: int, minor: int) -> None:
        self._run("attach", "-p", pid, "-m", minor)

    def detach(self, pid: int, minor: int) -> None:
        self._run("detach", "-p", pid, "-m", minor)

    def open(self, pid: int, minor: int, path: str, readonly: bool = False, no_o_direct: bool = False,
             timeout: int | None = None) -> None:
        """Open an image, given as "type:/path/to/file"."""
        args: list[object] = ["open", "-p", pid, "-m", minor, "-a", path]
        if readonly:
            args.append("-R")
        if no_o_direct:
            args.append("-D")
        if timeout is not None:
            args += ["-t", timeout]
        self._run(*args)

    def close(self, pid: int, minor: int, force: bool = False, timeout: int | None = None) -> None:
        args: list[object] = ["close", "-p", pid, "-m", minor]
        if force:
            args.append("-f")
        if timeout is not None:
            args += ["-t", timeout]
        self._run(*args)

    def pause(self, pid: int, minor: int, timeout: int | None = None) -> None:
        args: list[object] = ["pause", "-p", pid, "-m", minor]
        if timeout is not None:
            args += ["-t", timeout]
        self._run(*args)

    def unpause(self, pid: int, minor: int, path: str | None = None) -> None:
        """Resume the I/O, optionally reopening the image or switching to a new one."""
        args: list[object] = ["unpause", "-p", pid, "-m", minor]
        if path:
            args += ["-a", path]
        self._run(*args)

    def list(self, pid: int | None = None, minor: int | None = None) -> list[TapdiskInfo]:
        args: list[object] = ["list"]
        if pid is not None:
            args += ["-p", pid]
        if minor is not None:
            args += ["-m", minor]

        tapdisks = []
        for line in self._run(*args).strip().split('\n'):
            # Skip empty lines and the column headers ("pid minor state...") but not
            # the key=value lines ("pid=123...")
            if not line or (line.startswith('pid') and '=' not in line):
                continue

            # tap-ctl list has two output formats:
            # - key=value when not on a tty: "pid=123 minor=0 state=0 args=vhd:/path"
            # - columns on a tty: "PID MINOR STATE TYPE PATH"
            if '=' in line:
                fields = dict(part.split('=', 1) for part in line.split() if '=' in part)
                if 'pid' in fields:
                    args_str = fields.get('args', '')
                    img_type, img_path = args_str.split(':', 1) if ':' in args_str else ('', args_str)
                    tapdisks.append(TapdiskInfo(pid=int(fields['pid']),
                                                minor=int(fields.get('minor', -1)),
                                                state=fields.get('state', '-'),
                                                type=img_type,
                                                path=img_path))
            else:
                parts = line.split(None, 4)
                # skip the tapdisks without minor ("-")
                if len(parts) >= 4 and parts[1] != '-':
                    tapdisks.append(TapdiskInfo(pid=int(parts[0]),
                                                minor=int(parts[1]),
                                                state=parts[2],
                                                type=parts[3],
                                                path=parts[4] if len(parts) > 4 else ""))
        return tapdisks

    def stats(self, pid: int, minor: int) -> dict[str, Any]:
        result = self._run("stats", "-p", pid, "-m", minor)
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            # fall back on the Python dict syntax
            try:
                return ast.literal_eval(result)
            except Exception:
                raise TapCtlError(f"Could not parse stats output: {result}")

    def create(self, path: str, readonly: bool = False) -> tuple[int, int]:
        """Create a tapdisk (spawn, allocate, attach, open) for "type:/path", return (pid, minor)."""
        args: list[object] = ["create", "-a", path]
        if readonly:
            args.append("-R")

        # The output is the tapdev path (e.g. "/dev/xen/blktap-2/tapdev13"): the minor is
        # the trailing number, then look up the pid owning it (same method as connect-vbd.sh)
        result = self._run(*args)
        match = re.search(r'tapdev(\d+)$', result.strip())
        if not match:
            raise TapCtlError(f"Could not parse create output: {result}")
        minor = int(match.group(1))

        tapdisks = self.list(minor=minor)
        if not tapdisks:
            raise TapCtlError(f"No tapdisk found for minor {minor} after create")
        return tapdisks[0].pid, minor

    def destroy(self, pid: int, minor: int, timeout: int | None = None) -> None:
        args: list[object] = ["destroy", "-p", pid, "-m", minor]
        if timeout is not None:
            args += ["-t", timeout]
        self._run(*args)

class VBDConnectorError(Exception):
    pass

class VBDConnector:
    """
    Connect VBDs to running VMs through XenStore, without the toolstack.

    Must stay equivalent to connect-vbd.sh from blktap-tools, except where noted.
    """

    def __init__(self, host: Host) -> None:
        self.host = host
        self.tapctl = TapCtl(host)
        # the backend side, in the XenStore tree of dom0: the frontend side goes through the VM helpers
        self.xenstore = XenStoreHelper(host)
        # (VM uuid, device) -> (pid, minor) of the tapdisks created by connect(), so that
        # disconnect() can still destroy them when XenStore doesn't know them anymore
        self._tapdisks: dict[tuple[str, str], tuple[int, int]] = {}

    def calc_devid(self, device: str) -> int:
        """Device id of a device name (e.g. xvdb -> 51728)."""
        letter_num = ord(device.replace('xvd', '')) - ord('a')
        return (202 << 8) + (letter_num * 16)

    def frontend_path(self, device: str) -> str:
        """XenStore path of the frontend of the VBD, relative to the domain of the VM."""
        return f"device/vbd/{self.calc_devid(device)}"

    def backend_path(self, vm: VM, device: str) -> str:
        """XenStore path of the backend of the VBD, in dom0."""
        return f"/local/domain/0/backend/vbd3/{vm.param_get('dom-id')}/{self.calc_devid(device)}"

    @staticmethod
    def _wait_until(condition: Callable[[], bool], timeout: int) -> bool:
        """Wait for the condition, return whether it was met within the timeout."""
        try:
            wait_for(condition, timeout_secs=timeout, retry_delay_secs=1)
            return True
        except TimeoutError:
            return False

    @staticmethod
    def _state_reached(state: str | None, min_state: int) -> bool:
        return bool(state) and int(state) >= min_state

    def _wait_backend_state(self, backend: str, min_state: int, timeout: int) -> bool:
        return self._wait_until(lambda: self._state_reached(self.xenstore.read(f"{backend}/state"), min_state),
                                timeout)

    def _wait_frontend_state(self, vm: VM, frontend: str, min_state: int, timeout: int) -> bool:
        return self._wait_until(
            lambda: self._state_reached(vm.xenstore_read(f"{frontend}/state", accept_unknown_key=True), min_state),
            timeout)

    def _wait_closed(self, vm: VM, backend: str, frontend: str, timeout: int = 40) -> bool:
        """Wait for both backend and frontend to reach the Closed state (6)."""
        # The guest kernel can take around 30s to close the device, hence the
        # default timeout (same as connect-vbd.sh destroy)
        def states() -> tuple[str | None, str | None]:
            return (self.xenstore.read(f"{backend}/state"),
                    vm.xenstore_read(f"{frontend}/state", accept_unknown_key=True))
        if self._wait_until(lambda: states() == ("6", "6"), timeout):
            return True
        backend_state, frontend_state = states()
        logging.warning(f"Timeout waiting for Closed state: backend={backend_state} frontend={frontend_state}")
        return False

    def _list_minor(self, minor: int) -> list[TapdiskInfo]:
        # tap-ctl list fails when no tapdisk uses the minor
        try:
            return self.tapctl.list(minor=minor)
        except TapCtlError:
            return []

    def _minor_in_use(self, minor: int) -> bool:
        return bool(self._list_minor(minor))

    def _destroy_tapdisk(self, pid: int, minor: int) -> None:
        """Destroy a tapdisk, going on with the next steps on failure."""
        # XXX: tap-ctl destroy tend to be too quick and free might fails
        #      so call each steps manually
        logging.info(f"Destroying tapdisk: pid={pid} minor={minor}")
        for step in ("close", "detach", "free"):
            try:
                if step == "free":
                    self.tapctl.free(minor)
                else:
                    getattr(self.tapctl, step)(pid, minor)
            except Exception as e:
                logging.warning(f"tap-ctl {step} failed for pid={pid} minor={minor}: {e}")

    def _write_frontend_entries(self, vm: VM, domid: str, devid: int, frontend: str, backend: str) -> None:
        vm.xenstore_write(f"{frontend}/backend", backend)
        vm.xenstore_write(f"{frontend}/backend-id", "0")
        vm.xenstore_write(f"{frontend}/virtual-device", str(devid))
        vm.xenstore_write(f"{frontend}/device-type", "disk")
        vm.xenstore_write(f"{frontend}/state", "0")
        # tapback needs to read the frontend
        self.host.ssh(f'xenstore-chmod -r /local/domain/{domid}/{frontend} b{domid} b0')

    def _write_backend_entries(self, domid: str, backend: str, frontend: str,
                               device: str, phys: str, mode: str) -> None:
        self.xenstore.write(f"{backend}/frontend", f"/local/domain/{domid}/{frontend}")
        self.xenstore.write(f"{backend}/frontend-id", domid)
        self.xenstore.write(f"{backend}/online", "1")
        self.xenstore.write(f"{backend}/removable", "0")
        self.xenstore.write(f"{backend}/dev", device)
        self.xenstore.write(f"{backend}/mode", mode)
        self.xenstore.write(f"{backend}/device-type", "disk")
        # default XCP-ng polling values (same as connect-vbd.sh)
        self.xenstore.write(f"{backend}/polling-duration", "8000")  # 8 ms
        self.xenstore.write(f"{backend}/polling-idle-threshold", "50")  # 50%
        self.xenstore.write(f"{backend}/max-ring-page-order", "3")
        # multi-queue-max-queues is not written here: tapback advertises it when the
        # tapdisk driver supports multi-queue (commented out in connect-vbd.sh too)
        # writing physical-device triggers tapback
        self.xenstore.write(f"{backend}/physical-device", phys)
        self.host.ssh(f'xenstore-chmod -r {backend} b0 r{domid}')

    def _complete_xenbus_connection(self, vm: VM, backend: str, frontend: str) -> None:
        # tapback writes kthread-pid once it found the tapdisk
        if not self._wait_until(lambda: self.xenstore.exists(f"{backend}/kthread-pid"), timeout=10):
            raise VBDConnectorError("Tapback timeout: kthread-pid not created")

        self.xenstore.write(f"{backend}/hotplug-status", "connected")
        vm.xenstore_write(f"{frontend}/state", "1")  # Initialising
        # Set the backend state to 1 only if tapback hasn't already advanced it
        # (SSH latency can cause tapback to advance before we get here)
        current_state = self.xenstore.read(f"{backend}/state")
        if current_state and int(current_state) > 1:
            logging.info(f"Backend already advanced to state {current_state}, skipping state=1 write")
        else:
            self.xenstore.write(f"{backend}/state", "1")  # Initialising

        # tapback switches to InitWait (2) when it sees frontend=1 and hotplug-status=connected
        if not self._wait_backend_state(backend, 2, timeout=10):
            final_state = self.xenstore.read(f"{backend}/state")
            logging.error(f"Backend did not advance past state 1 (final state: {final_state})")
            tapback_status = self.host.ssh('systemctl is-active tapback', check=False)
            logging.error(f"Tapback status: {tapback_status}")
            raise VBDConnectorError("Backend did not advance past state 1")
        # blkfront sets up the rings and switches to Initialised (3)
        if not self._wait_frontend_state(vm, frontend, 3, timeout=15):
            raise VBDConnectorError("Frontend did not setup rings")
        # then both switch to Connected (4)
        if not self._wait_backend_state(backend, 4, timeout=15):
            raise VBDConnectorError("Backend did not reach Connected state")
        if not self._wait_frontend_state(vm, frontend, 4, timeout=15):
            raise VBDConnectorError("Frontend did not reach Connected state")

    def connect(self, vm: VM, image: str, device: str, readonly: bool = False) -> tuple[int, int]:
        """Create a tapdisk for image ("type:/path/to/file") and connect it to the VM, return (pid, minor)."""
        logging.info(f"Connecting {device}: {image} (readonly={readonly})")
        # Check the VM is running before creating the tapdisk (like connect-vbd.sh)
        if not vm.is_running():
            raise VBDConnectorError(f"VM {vm.uuid} not running")

        pid, minor = self.tapctl.create(image, readonly=readonly)
        try:
            self.attach(vm, pid, minor, device, readonly=readonly)
        except Exception as e:
            logging.error(f"Connection failed, cleaning up tapdisk: {e}")
            self._destroy_tapdisk(pid, minor)
            raise VBDConnectorError(f"Failed to connect VBD: {e}")

        self._tapdisks[(vm.uuid, device)] = (pid, minor)
        logging.info(f"Successfully connected {device}")
        return pid, minor

    def disconnect(self, vm: VM, device: str) -> None:
        """Disconnect a VBD from the VM and destroy its tapdisk."""
        pid: int | None = None
        minor: int | None = None
        backend = frontend = None

        if not vm.is_running():
            logging.warning(f"VM {vm.uuid} not running: no XenStore entries to clean up")
        else:
            backend = self.backend_path(vm, device)
            frontend = self.frontend_path(device)
            if not self.xenstore.exists(backend):
                logging.warning(f"Device {device} not connected to VM")
                backend = frontend = None

        if backend is not None and frontend is not None:
            # find the tapdisk from the physical-device ("hex_major:hex_minor")
            phys = self.xenstore.read(f"{backend}/physical-device")
            if phys:
                try:
                    _, minor_hex = phys.split(':')
                    minor = int(minor_hex, 16)
                    tapdisks = self.tapctl.list(minor=minor)
                    if tapdisks:
                        pid = tapdisks[0].pid
                except Exception as e:
                    logging.warning(f"Could not determine tapdisk info: {e}")

            # switch to Closing, and let tapback and blkfront tear down the connection
            try:
                self.xenstore.write(f"{backend}/state", "5")
            except Exception:
                pass
            self._wait_closed(vm, backend, frontend)
            # give tapback a bit more time to finish cleanup
            time.sleep(0.5)

        # Fall back on the tapdisk created by connect() when XenStore doesn't know it
        # (VM stopped, device already detached...), if its minor is still owned by it
        known = self._tapdisks.pop((vm.uuid, device), None)
        if pid is None and known is not None:
            known_pid, known_minor = known
            if any(t.pid == known_pid for t in self._list_minor(known_minor)):
                pid, minor = known_pid, known_minor

        # Destroy the tapdisk before removing XenStore entries (same order as connect-vbd.sh)
        if pid is not None and minor is not None:
            self._destroy_tapdisk(pid, minor)

        if backend is not None and frontend is not None:
            self.xenstore.rm(backend)
            vm.xenstore_rm(frontend, accept_unknown_key=True)

        if minor is not None and self._minor_in_use(minor):
            raise VBDConnectorError(f"Minor {minor} still in use after disconnecting {device}")

        logging.info(f"Disconnected {device}")

    def attach(self, vm: VM, pid: int, minor: int, device: str, readonly: bool = True) -> None:
        """Connect an existing tapdisk to the VM."""
        if not vm.is_running():
            raise VBDConnectorError(f"VM {vm.uuid} not running")
        domid = vm.param_get('dom-id')
        devid = self.calc_devid(device)
        backend = f"/local/domain/0/backend/vbd3/{domid}/{devid}"
        frontend = self.frontend_path(device)

        # Refuse to replace a device still in use: unlike connect-vbd.sh, which
        # removes the entries unconditionally, silently tearing down the device
        # in the guest and leaking its tapdisk
        backend_state = self.xenstore.read(f"{backend}/state")
        if backend_state is not None and backend_state != "6":
            raise VBDConnectorError(
                f"Device {device} already connected to VM {vm.uuid} (backend state {backend_state})")

        # clean up leftover entries (closed or incomplete)
        self.xenstore.rm(backend)
        vm.xenstore_rm(frontend, accept_unknown_key=True)

        tapdev = f"/dev/xen/blktap-2/tapdev{minor}"
        if self.host.ssh_with_result(f'test -e {tapdev}').returncode != 0:
            raise VBDConnectorError(f"Tapdisk device {tapdev} not found")
        phys = self.host.ssh(f'stat -L --format=%t:%T {tapdev}').strip()

        mode = "r" if readonly else "w"
        self._write_frontend_entries(vm, domid, devid, frontend, backend)
        self._write_backend_entries(domid, backend, frontend, device, phys, mode)
        self._complete_xenbus_connection(vm, backend, frontend)

        logging.info(f"Attached {device} (devid={devid}, readonly={readonly})")

    def detach(self, vm: VM, device: str) -> None:
        """Disconnect a VBD from the VM, without destroying its tapdisk."""
        if not vm.is_running():
            logging.warning(f"VM {vm.uuid} not running: nothing to detach")
            return
        backend = self.backend_path(vm, device)
        frontend = self.frontend_path(device)

        if not self.xenstore.exists(backend):
            logging.warning(f"Device {device} not connected to VM")
            return

        # switch to Closing, and let tapback and blkfront tear down the connection
        try:
            self.xenstore.write(f"{backend}/state", "5")
        except Exception:
            pass
        self._wait_closed(vm, backend, frontend)
        # give tapback time to cleanup
        time.sleep(0.3)

        self.xenstore.rm(backend)
        vm.xenstore_rm(frontend, accept_unknown_key=True)

        logging.info(f"Detached {device} (tapdisk still running)")

    def status(self, vm: VM, device: str) -> str:
        """Human readable state of a VBD connection."""
        if not vm.is_running():
            return f"VM {vm.uuid} not running"
        devid = self.calc_devid(device)
        backend = self.backend_path(vm, device)
        frontend = self.frontend_path(device)

        status_lines = [f"Device: {device} (devid={devid})", f"Backend: {backend}"]
        if self.xenstore.exists(backend):
            status_lines.append(f"  State: {self.xenstore.read(f'{backend}/state')}")
            status_lines.append(f"  Mode: {self.xenstore.read(f'{backend}/mode')}")
            status_lines.append(f"  Physical device: {self.xenstore.read(f'{backend}/physical-device')}")
        else:
            status_lines.append("  (not connected)")

        status_lines.append(f"Frontend: {frontend}")
        frontend_state = vm.xenstore_read(f"{frontend}/state", accept_unknown_key=True)
        if frontend_state is not None:
            status_lines.append(f"  State: {frontend_state}")
        else:
            status_lines.append("  (not connected)")

        return "\n".join(status_lines)

class XenStoreError(Exception):
    pass

class XenStoreHelper:
    """Wrapper for the xenstore-* commands."""

    def __init__(self, host: Host) -> None:
        self.host = host

    def _run(self, cmd: str, *args: object, check: bool = True) -> str:
        try:
            return self.host.ssh(shlex.join([cmd] + [str(arg) for arg in args]), check=check, simple_output=True)
        except Exception as e:
            if check:
                raise XenStoreError(f"{cmd} {' '.join(str(a) for a in args)} failed: {e}")
            return ""

    def read(self, path: str) -> str | None:
        """Value of the key, or None if it doesn't exist."""
        # rely on the return code: stderr is merged in the output, so on a missing
        # key the output is the error message, not an empty string
        result = self.host.ssh_with_result(shlex.join(["xenstore-read", path]))
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def write(self, path: str, value: str) -> None:
        self._run("xenstore-write", path, value)

    def rm(self, path: str) -> None:
        self._run("xenstore-rm", path, check=False)

    def exists(self, path: str) -> bool:
        try:
            self._run("xenstore-exists", path, check=True)
            return True
        except XenStoreError:
            return False

    def ls(self, path: str) -> list[str]:
        """Direct children of a path (empty if it doesn't exist)."""
        # xenstore-list, not xenstore-ls which is recursive; and rely on the return
        # code as stderr is merged in the output
        result = self.host.ssh_with_result(shlex.join(["xenstore-list", path]))
        if result.returncode != 0:
            return []
        return result.stdout.split()
