from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from itertools import islice

import paramiko
import pyte

from lib.boot import customize_grub, customize_isolinux
from lib.commands import ssh
from lib.vm import VM

from typing import Any, Iterator, Self

BUFFER_READ_SIZE = 4096
logging.getLogger("paramiko").setLevel(logging.WARNING)

class IgnorePolicy(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        pass

# Regex for `/tmp/install-log``
INSTALL_FAILED = re.compile(r"INFO\s+\[[-0-9 :]+\] INSTALL FAILED\.")
NEW_PHASE_READING_PACKAGE_INFORMATION = re.compile(r"DISPATCH: NEW PHASE: Reading package information")
NEW_PHASE_COMPLETING_INSTALLATION = re.compile(r"DISPATCH: NEW PHASE: Completing installation")
INSTALLATION_COMPLETED_SUCCESSFULLY = re.compile(r"The installation completed successfully")
RESTORING_BACKUP = re.compile(r"Restoring backup")
DATA_RESTORATION_COMPLETE = re.compile(r"Data restoration complete.  About to re-install bootloader.")

class ScreenError(Exception):
    pass

@dataclass
class InstallerVM:
    vm: VM
    residence_client: paramiko.SSHClient
    console_channel: paramiko.Channel
    console_screen: pyte.Screen
    console_stream: pyte.ByteStream
    ssh_client: paramiko.SSHClient | None = None
    stabilize_time: float = 1.0

    @classmethod
    def connect(cls, vm: VM):
        # Get the domain corresponding to the host VM
        residence_host = vm.get_residence_host()
        dom_id = int(residence_host.xe(
            'vm-param-get',
            {'uuid': vm.uuid, 'param-name': 'dom-id'},
        ))

        # Connect to residence host
        residence_client = paramiko.SSHClient()
        logging.info(f"Open an SSH channel to {residence_host.hostname_or_ip}")
        residence_client.set_missing_host_key_policy(IgnorePolicy())
        residence_client.connect(residence_host.hostname_or_ip, username='root')
        residence_transport = residence_client.get_transport()
        assert residence_transport is not None

        # Allocate a pty and connect it to the serial line of the installer VM
        logging.info(f"Connecting to installer VM using serial line in domain {dom_id} on {residence_host}")
        console_channel = residence_transport.open_session()
        columns, lines = (100, 32) if vm.is_uefi else (80, 24)
        console_channel.get_pty(term='vt100', width=columns, height=lines)
        command = f"xl console -t serial {dom_id}"
        logging.debug(f"Run command {command!r}")
        console_channel.exec_command(command.encode())
        console_channel.settimeout(30.0)

        # Prepare terminal emulator
        screen = pyte.Screen(columns=columns, lines=lines)
        screen.define_charset("U", "(")
        stream = pyte.ByteStream(screen)
        stream.select_other_charset("@")

        return cls(vm, residence_client, console_channel, screen, stream)

    def cleanup(self):
        if self.ssh_client is not None:
            self.ssh_client.close()
        self.console_channel.close()
        self.residence_client.close()

    def customize_boot(self, vmlinuz_config: str) -> None:
        if self.vm.is_uefi:
            customize_grub(self, vmlinuz_config)
        else:
            customize_isolinux(self, vmlinuz_config)

    def ssh_connect_with_ci_key(self):
        from data import HOST_DEFAULT_PASSWORD, TEST_SSH_PUBKEY

        # Add CI key
        assert self.vm.ip is not None
        logging.info(f"Add CI keys to {self.vm.ip}")
        with paramiko.SSHClient() as password_client:
            password_client.set_missing_host_key_policy(IgnorePolicy())
            password_client.connect(self.vm.ip, username='root', password=HOST_DEFAULT_PASSWORD)
            stdin, stdout, stderr = password_client.exec_command(
                f'mkdir /root/.ssh && echo "{TEST_SSH_PUBKEY}" > /root/.ssh/authorized_keys'
            )
            exit_status = stdout.channel.recv_exit_status()
            assert exit_status == 0

        # Connect with pubkey authentication
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client .set_missing_host_key_policy(IgnorePolicy())
        self.ssh_client .connect(self.vm.ip, username='root')

    def refresh_screen(self):
        while self.console_channel.recv_ready():
            self.console_stream.feed(self.console_channel.recv(BUFFER_READ_SIZE))

    def has_content_on_screen(self, pattern: str) -> bool:
        return any(pattern in line for line in self.console_screen.display)

    def wait_for_screen_content(self, pattern: str) -> None:
        while not self.has_content_on_screen(pattern):
            self.console_stream.feed(self.console_channel.recv(BUFFER_READ_SIZE))

    def wait_for_screen_to_settle(self) -> None:
        time.sleep(self.stabilize_time)
        self.refresh_screen()

    def wait_for_screen_to_stabilize(self) -> None:
        time.sleep(self.stabilize_time)
        while self.console_channel.recv_ready():
            self.refresh_screen()
            time.sleep(self.stabilize_time)

    def send_to_console(self, data: str) -> None:
        self.console_channel.send(data.encode())

    def screen_error(self, message: str) -> ScreenError:
        return ScreenError(f"{message}:\n{self.show_screen()}")

    def debug_screen(self, title: str) -> None:
        logging.debug(f"{title}:\n{self.show_screen()}")

    def show_screen(self) -> str:
        ANSI_RESET = "\033[0m"
        ANSI_BOLD = "\033[1m"
        ANSI_REVERSE = "\033[7m"

        columns = self.console_screen.columns

        # 1. Draw the top border
        result = ["┌" + "─" * columns + "┐"]

        # 2. Draw each row with left and right borders
        for row_idx in range(self.console_screen.lines):
            row = self.console_screen.buffer[row_idx]

            # Start with the left border wall
            row_str = "│"

            for col_idx in range(columns):
                char = row[col_idx]

                fmt = ""
                if char.bold:
                    fmt += ANSI_BOLD
                if char.reverse:
                    fmt += ANSI_REVERSE

                if fmt:
                    row_str += f"{fmt}{char.data}{ANSI_RESET}"
                else:
                    row_str += char.data

            # Cap the line with the right border wall
            row_str += "│"

            result.append(row_str)

        # 3. Draw the bottom border
        result.append("└" + "─" * columns + "┘")
        return "\n".join(result)

class InstallationFailed(Exception):
    @classmethod
    def check(cls, line: str, following_lines: Iterator[str]) -> None:
        if not INSTALL_FAILED.search(line):
            return
        lines = [line]
        if isinstance(following_lines, paramiko.ChannelFile):
            following_lines.channel.settimeout(1)
        try:
            for line in following_lines:
                lines.append(line)
        except TimeoutError:
            pass
        raise cls("\n".join(lines))


class AnswerFile:
    def __init__(self, kind: str, /):
        from data import BASE_ANSWERFILES
        defn = BASE_ANSWERFILES[kind]
        self.defn = self._normalize_structure(defn)  # type: ignore

    def write_xml(self, filename: str) -> None:
        etree = ET.ElementTree(self._defn_to_xml_et(self.defn))
        etree.write(filename)

    # chainable mutators for lambdas

    def top_append(self, *defs: dict[str, Any] | None) -> Self:
        for defn in defs:
            if defn is None:
                continue
            self.defn['CONTENTS'].append(self._normalize_structure(defn))
        return self

    def top_setattr(self, attrs: dict[str, Any]) -> Self:
        assert 'CONTENTS' not in attrs
        self.defn.update(attrs)
        return self

    # makes a mutable deep copy of all `contents`
    @staticmethod
    def _normalize_structure(defn: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(defn, dict), f"{defn!r} is not a dict"
        assert 'TAG' in defn, f"{defn} has no TAG"

        # type mutation through nearly-shallow copy
        new_defn: dict[str, Any] = {
            'TAG': defn['TAG'],
            'CONTENTS': [],
        }
        for key, value in defn.items():
            if key == 'CONTENTS':
                if isinstance(value, str):
                    new_defn['CONTENTS'] = value
                else:
                    new_defn['CONTENTS'] = [
                        AnswerFile._normalize_structure(item)
                        for item in value
                        if item is not None
                    ]
            elif key == 'TAG':
                pass            # already copied
            else:
                new_defn[key] = value

        return new_defn

    # convert to a ElementTree.Element tree suitable for further
    # modification before we serialize it to XML
    @staticmethod
    def _defn_to_xml_et(defn: dict[str, Any], *, parent: ET.Element | None = None) -> ET.Element:
        assert isinstance(defn, dict)
        defn = dict(defn)
        name = defn.pop('TAG')
        assert isinstance(name, str)
        contents = defn.pop('CONTENTS', ())
        assert isinstance(contents, (str, list))
        element = ET.Element(name, {}, **defn)
        if parent is not None:
            parent.append(element)
        if isinstance(contents, str):
            element.text = contents
        else:
            for content in contents:
                AnswerFile._defn_to_xml_et(content, parent=element)
        return element


@dataclass
class LogLineWaitTracker:
    max_reported_waits: int = 5
    reported_wait_threshold: float = 10.0
    previous_timestamp: float | None = None
    previous_line: str | None = None
    longest_waits: list[tuple[float, float, str, str]] = field(default_factory=list)

    def track(self, lines: Iterator[str], *, ignore_previous_line_timestamp: bool = False) -> Iterator[str]:
        if ignore_previous_line_timestamp:
            self.previous_timestamp = None
            self.previous_line = None
        for line in lines:
            self.record(line)
            yield line

    def record(self, line: str) -> None:
        line = line.removesuffix("\n")
        now = time.monotonic()
        previous = self.previous_timestamp
        previous_line = self.previous_line
        self.previous_timestamp = now
        self.previous_line = line
        if previous is None:
            return

        assert previous_line is not None
        delay = now - previous
        if delay >= self.reported_wait_threshold:
            logging.info(
                f"Observed {delay:.1f}s wait between the following log lines:\n"
                f"  > {previous_line}\n"
                f"  > {line}"
            )

        self.longest_waits.append((delay, now, previous_line, line))
        self.longest_waits.sort(reverse=True)
        del self.longest_waits[self.max_reported_waits:]
        self.longest_waits.sort(key=lambda x: x[1])

    def log_longest_waits(self, *, context: str) -> None:
        if not self.longest_waits:
            logging.debug(f"Longest waits for {context}: no live log lines received")
            return

        waits = "\n".join(
            f"  * {delay:.1f}s between:\n"
            f"    > {before}\n"
            f"    > {after}"
            for delay, _, before, after in self.longest_waits
        )
        logging.debug(f"Longest waits for {context}:\n{waits}")

def poweroff(ip: str) -> None:
    ssh(ip, "nohup sh -c 'sleep 2 && poweroff' >/dev/null 2>&1 &")


def monitor_install(installer: InstallerVM) -> None:
    assert installer.ssh_client is not None
    wait_tracker = LogLineWaitTracker()

    logging.info("Get the PID of the installer")
    stdin, stdout, stderr = installer.ssh_client.exec_command("pgrep -f -n 'python /opt/xensource/installer/init'")
    pid_string = stdout.read().decode().strip()
    assert stdout.channel.recv_exit_status() == 0, stderr.read().decode()
    pid = int(pid_string)

    logging.info("Check content of /tmp/install-log")
    stdin, stdout, stderr = installer.ssh_client.exec_command("cat /tmp/install-log")
    content = stdout.read()
    length = len(content)
    assert stdout.channel.recv_exit_status() == 0, stderr.read().decode()

    lines = [raw_line.decode() for raw_line in content.splitlines()]
    package_phase_started = False
    for i, line in enumerate(lines):
        logging.debug(f"> {line.rstrip()}")
        if NEW_PHASE_READING_PACKAGE_INFORMATION.search(line):
            package_phase_started = True
        InstallationFailed.check(line, islice(lines, i + 1, None))

    if package_phase_started:
        logging.info("Install preparation succeeded")

    logging.info(f"Start monitoring /tmp/install-log with PID {pid} on {installer.vm.ip}")
    stdin, stdout, stderr = installer.ssh_client.exec_command(
        f"tail -f /tmp/install-log --pid {pid} -c +{length+1}",
        timeout=30 * 60,
        bufsize=1,
    )

    if not package_phase_started:
        for line in wait_tracker.track(stdout):
            logging.debug(f"> {line.rstrip()}")
            InstallationFailed.check(line, stdout)
            if NEW_PHASE_READING_PACKAGE_INFORMATION.search(line):
                break
        logging.info("Install preparation succeeded")

    for line in wait_tracker.track(stdout):
        logging.debug(f"> {line.rstrip()}")
        InstallationFailed.check(line, stdout)
        if NEW_PHASE_COMPLETING_INSTALLATION.search(line):
            break
    logging.info("RPM installation succeeded")

    for line in wait_tracker.track(stdout):
        logging.debug(f"> {line.rstrip()}")
        InstallationFailed.check(line, stdout)
        if INSTALLATION_COMPLETED_SUCCESSFULLY.search(line):
            break
    logging.info("System installation succeeded")

    # Decrease back to 1 minute
    stdout.channel.settimeout(60)

    for line in wait_tracker.track(stdout):
        logging.debug(f"> {line.rstrip()}")
        InstallationFailed.check(line, stdout)
    logging.info("The installer process has terminated")
    wait_tracker.log_longest_waits(context=f"install on {installer.vm.ip}")

def monitor_restore(installer: InstallerVM) -> None:
    assert installer.ssh_client is not None
    wait_tracker = LogLineWaitTracker()

    logging.info("Get the PID of the installer")
    stdin, stdout, stderr = installer.ssh_client.exec_command("pgrep -f -n 'python /opt/xensource/installer/init'")
    pid_string = stdout.read().decode().strip()
    assert stdout.channel.recv_exit_status() == 0, stderr.read().decode()
    pid = int(pid_string)

    logging.info("Check content of /tmp/install-log")
    stdin, stdout, stderr = installer.ssh_client.exec_command("cat /tmp/install-log")
    content = stdout.read()
    length = len(content)
    assert stdout.channel.recv_exit_status() == 0, stderr.read().decode()

    lines = [raw_line.decode() for raw_line in content.splitlines()]
    restoring_backup_started = False
    for i, line in enumerate(lines):
        logging.debug(f"> {line.rstrip()}")
        if RESTORING_BACKUP.search(line):
            restoring_backup_started = True
        InstallationFailed.check(line, islice(lines, i + 1, None))

    if restoring_backup_started:
        logging.info("Restoring backup started")

    logging.info(f"Start monitoring /tmp/install-log with PID {pid} on {installer.vm.ip}")
    stdin, stdout, stderr = installer.ssh_client.exec_command(
        f"tail -f /tmp/install-log --pid {pid} -c +{length+1}",
        timeout=2 * 60,
        bufsize=1,
    )

    if not restoring_backup_started:
        for line in wait_tracker.track(stdout):
            logging.debug(f"> {line.rstrip()}")
            InstallationFailed.check(line, stdout)
            if RESTORING_BACKUP.search(line):
                break
        logging.info("Restoring backup started")

    # Increase timeout to 5 minutes for this section
    stdout.channel.settimeout(5 * 60)

    for line in wait_tracker.track(stdout):
        logging.debug(f"> {line.rstrip()}")
        InstallationFailed.check(line, stdout)
        if DATA_RESTORATION_COMPLETE.search(line):
            break
    logging.info("Data restoration succeeded")

    # Refresh screen
    installer.refresh_screen()
    installer.debug_screen("Restore completing screen")

    # Wait for restore completed screen
    logging.info("Wait for restore completed screen")
    installer.wait_for_screen_content("┤ Restore Complete ├")
    installer.wait_for_screen_to_stabilize()
    installer.debug_screen("Restore completed screen:\n{show_screen(screen)}")

    installer.send_to_console("\r")

    # Decrease back to 1 minute
    stdout.channel.settimeout(60)

    for line in wait_tracker.track(stdout, ignore_previous_line_timestamp=True):
        logging.debug(f"> {line.rstrip()}")
        InstallationFailed.check(line, stdout)
    logging.info("The installer process has terminated")
    wait_tracker.log_longest_waits(context=f"restore on {installer.vm.ip}")
