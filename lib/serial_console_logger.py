"""
Serial Console Logger - Background logging of VM serial console output

This module provides facilities to automatically log VM serial console output
during test execution via SSH subprocesses running `xl console`.
"""

from __future__ import annotations

import datetime
import logging
import os
import subprocess
import threading

from typing import TYPE_CHECKING, Dict, Optional, Set

if TYPE_CHECKING:
    from lib.host import Host
    from lib.vm import VM


class SerialConsoleLogger:
    """
    Manages background SSH processes that log VMs serial console output.
    Can log and monitor multiple VMs at a time.

    This class handles:
    - Starting SSH subprocesses with `xl console` for each VM
    - Tracking VM domids and detecting changes
    - Background monitoring to detect domid changes during tests
    - Writing console output to log files
    """

    def __init__(self, log_dir: str, scope: str = "session",
                 enable_monitoring: bool = True, monitor_interval: float = 5.0,
                 keep_log: bool = True):

        self.log_dir = log_dir
        self.scope = scope
        self.keep_log = keep_log
        self.processes: Dict[str, subprocess.Popen | None] = {}  # vm_uuid -> Popen
        self.vm_domids: Dict[str, str] = {}  # vm_uuid -> domid
        self.vm_hosts: Dict[str, Host] = {}  # vm_uuid -> Host
        self.log_files: Dict[str, str] = {}  # vm_uuid -> log_file_path

        # Background monitoring
        self.enable_monitoring = enable_monitoring
        self.monitor_interval = monitor_interval
        self.monitor_thread: Optional[threading.Thread] = None
        self.monitor_stop_event = threading.Event()

        logging.debug(
            f"SerialConsoleLogger initialized (scope={scope}, log_dir={log_dir}, "
            f"monitoring={enable_monitoring}, interval={monitor_interval}s)"
        )

        # Start monitoring if enabled
        if self.enable_monitoring:
            self.start_monitoring()

    def _get_log_filename(self, vm_uuid: str, host: Host) -> str:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        host_ip_safe = host.hostname_or_ip.replace(':', '_').replace('.', '_')
        vm_uuid_short = vm_uuid[:8]

        filename = f"{host_ip_safe}_vm_{vm_uuid_short}_serial_{timestamp}.log"
        return os.path.join(self.log_dir, filename)

    def _vm_param_get(self, vm_uuid: str, param: str):
        host = self.vm_hosts.get(vm_uuid)
        assert host
        return host.xe(
            'vm-param-get',
            {'uuid': vm_uuid, 'param-name': param},
            minimal=True
        )

    def add_vm(self, vm: VM, host: Optional[Host] = None) -> bool:
        if vm.uuid in self.processes:
            return True

        if host is None:
            host = vm.host

        assert host is not None, "Host must be provided or VM must have a host"

        if not vm.is_running():
            return False

        domid = vm.param_get("dom-id")
        if not domid or domid == '-1':
            return False

        return self._start_logging(vm.uuid, host, domid)

    def remove_vm(self, vm: VM) -> None:
        self._remove_vm_by_uuid(vm.uuid)

    # Background monitoring methods

    def start_monitoring(self) -> None:
        if self.monitor_thread is not None:
            return

        logging.debug(f"Starting background monitoring thread (interval={self.monitor_interval}s)")
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name=f"SerialConsoleMonitor-{self.scope}"
        )
        self.monitor_thread.start()

    def _monitor_loop(self) -> None:
        while not self.monitor_stop_event.wait(self.monitor_interval):
            # Get snapshot of current VMs being logged
            vm_uuids = list(self.processes.keys())

            for vm_uuid in vm_uuids:
                try:
                    self._check_vm_state_by_uuid(vm_uuid)
                except Exception as e:
                    # Ignore monitoring errors for individual VMs
                    logging.warning(f"something went wrong: {e}")
                    pass

    def _check_vm_state_by_uuid(self, vm_uuid: str):
        host = self.vm_hosts.get(vm_uuid)
        process = self.processes.get(vm_uuid)
        tracked_domid = self.vm_domids.get(vm_uuid)

        logging.warning(f"check VM state: {host} {process} {tracked_domid}")
        assert host

        # Check VM power state
        power_state = self._vm_param_get(vm_uuid, 'power-state')
        if power_state != 'running':
            # VM not running, stop logging
            logging.warning(f"stopping: state={power_state}")
            self._remove_vm_by_uuid(vm_uuid)
            return

        # If we don't have process here
        if not process:
            try:
                current_domid = self._vm_param_get(vm_uuid, 'dom-id')
                if current_domid and current_domid != '-1':
                    # VM is running, try to start logging
                    self._restart_logging_by_uuid(vm_uuid)
                return
            except Exception:
                logging.warning("restart failed, retry later")
                return

        try:
            try:
                process.poll()
            except Exception:
                logging.debug(
                    f"[monitor] Serial console process for VM {vm_uuid[:8]} "
                    f"terminated (exit code {process.returncode}), restarting"
                )
                self._restart_logging_by_uuid(vm_uuid)
                return True

            current_domid = self._vm_param_get(vm_uuid, 'dom-id')

            # Check if domid is -1 (VM doesn't exist or was destroyed)
            if not current_domid:
                # VM destroyed or halted (domid=-1), stop logging
                self._remove_vm_by_uuid(vm_uuid)
                return False

            # Both domid can be -1, if VM is not started yet
            if current_domid != -1 and current_domid != tracked_domid:
                logging.debug(
                    f"[monitor] VM {vm_uuid[:8]} domid changed from {tracked_domid} "
                    f"to {current_domid}, restarting serial console logging"
                )
                return self._restart_logging_by_uuid(vm_uuid)

            return True

        except Exception:
            return False

    def _restart_logging_by_uuid(self, vm_uuid: str) -> bool:
        host = self.vm_hosts.get(vm_uuid)
        if not host:
            return False

        self._stop_logging(vm_uuid)

        try:
            domid = self._vm_param_get(vm_uuid, 'dom-id')
        except Exception:
            return False
        if domid == '-1' or not domid:
            return False

        success = self._start_logging(vm_uuid, host, domid)
        return success

    def _start_logging(self, vm_uuid: str, host: Host, domid: str) -> bool:
        try:
            os.makedirs(self.log_dir, exist_ok=True)

            ssh_cmd = [
                "ssh", "-t",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR",
                f"{host.user}@{host.hostname_or_ip}",
                f"xl console -t serial {domid} 2> /dev/null"
            ]

            # Fill dictionnaries before running command in case VM is not
            # started yet
            self.processes[vm_uuid] = None
            self.vm_domids[vm_uuid] = domid
            self.vm_hosts[vm_uuid] = host

            if self.keep_log and self.log_files.get(vm_uuid):
                log_file = self.log_files[vm_uuid]
            else:
                log_file = self._get_log_filename(vm_uuid, self.vm_hosts[vm_uuid])
            self.log_files[vm_uuid] = log_file

            logging.debug(f"Starting serial console logging for VM {vm_uuid[:8]} (domid={domid}) -> {log_file}")

            with open(log_file, 'a') as log_fh:
                process = subprocess.Popen(
                    ssh_cmd,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL
                )
            self.processes[vm_uuid] = process
            return True

        except Exception as e:
            logging.warning(f"Failed to start process: {e}")
            return False

    def _stop_logging(self, vm_uuid: str) -> bool:
        if vm_uuid not in self.processes:
            return True

        process = self.processes[vm_uuid]
        log_file = self.log_files[vm_uuid]

        try:
            assert process

            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logging.warning(f"Process {process.pid} didn't terminate, force killing")
                process.kill()
                process.wait()

            logging.debug(f"Stopped serial console logging for VM {vm_uuid[:8]}, log: {log_file}")
            self.processes[vm_uuid] = None

            if os.stat(log_file).st_size == 0:
                os.unlink(log_file)

            return True
        except Exception:
            logging.warning("Exception received will stopping logging")
            return False

    def _remove_vm_by_uuid(self, vm_uuid: str) -> None:
        logging.warning(f"Remove VM: {vm_uuid}")
        if self._stop_logging(vm_uuid):

            if vm_uuid in self.processes:
                del self.processes[vm_uuid]
            if vm_uuid in self.vm_domids:
                del self.vm_domids[vm_uuid]
            if vm_uuid in self.vm_hosts:
                del self.vm_hosts[vm_uuid]
            if vm_uuid in self.log_files:
                del self.log_files[vm_uuid]

    def cleanup(self) -> None:
        """Stop monitoring thread and all logging processes."""
        logging.warning("Monitor cleanup called")

        # Stop monitoring thread first
        if self.monitor_thread is not None:
            self.monitor_stop_event.set()

            # Wait for thread to finish (max 2 seconds)
            self.monitor_thread.join(timeout=2.0)

            if self.monitor_thread.is_alive():
                logging.warning("Monitoring thread did not stop cleanly")

            self.monitor_thread = None

        # Make a copy of the list since we'll be modifying it
        vm_uuids = list(self.processes.keys())

        for vm_uuid in vm_uuids:
            self._remove_vm_by_uuid(vm_uuid)

    def get_logged_vms(self) -> Set[str]:
        """Return set of VM UUIDs currently being logged."""
        return set(self.processes.keys())
