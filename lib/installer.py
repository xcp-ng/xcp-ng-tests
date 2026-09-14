from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from itertools import islice

import paramiko
import pyte

from lib.boot import BUFFER_READ_SIZE, show_screen
from lib.commands import ssh
from lib.vm import VM

from typing import Any, Iterator, Self

INSTALL_FAILED = re.compile(r"INFO\s+\[[-0-9 :]+\] INSTALL FAILED\.")
NEW_PHASE_READING_PACKAGE_INFORMATION = re.compile(r"DISPATCH: NEW PHASE: Reading package information")
NEW_PHASE_COMPLETING_INSTALLATION = re.compile(r"DISPATCH: NEW PHASE: Completing installation")
INSTALLATION_COMPLETED_SUCCESSFULLY = re.compile(r"The installation completed successfully")
RESTORING_BACKUP = re.compile(r"Restoring backup")
DATA_RESTORATION_COMPLETE = re.compile(r"Data restoration complete.  About to re-install bootloader.")


@dataclass
class InstallerVM:
    vm: VM
    console_channel: paramiko.Channel
    ssh_client: paramiko.SSHClient

class IgnorePolicy(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        pass


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

def poweroff(ip: str) -> None:
    ssh(ip, "nohup sh -c 'sleep 2 && poweroff' >/dev/null 2>&1 &")


def monitor_install(installer: InstallerVM) -> None:
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
        timeout=2 * 60,
        bufsize=1,
    )

    if not package_phase_started:
        for line in stdout:
            logging.debug(f"> {line.rstrip()}")
            InstallationFailed.check(line, stdout)
            if NEW_PHASE_READING_PACKAGE_INFORMATION.search(line):
                break
        logging.info("Install preparation succeeded")

    for line in stdout:
        logging.debug(f"> {line.rstrip()}")
        InstallationFailed.check(line, stdout)
        if NEW_PHASE_COMPLETING_INSTALLATION.search(line):
            break
    logging.info("RPM installation succeeded")

    # Increase timeout to 8 minutes for this section
    stdout.channel.settimeout(8 * 60)

    for line in stdout:
        logging.debug(f"> {line.rstrip()}")
        InstallationFailed.check(line, stdout)
        if INSTALLATION_COMPLETED_SUCCESSFULLY.search(line):
            break
    logging.info("System installation succeeded")

    # Decrease back to 1 minute
    stdout.channel.settimeout(60)

    for line in stdout:
        logging.debug(f"> {line.rstrip()}")
        InstallationFailed.check(line, stdout)
    logging.info("The installer process has terminated")

def monitor_restore(installer: InstallerVM) -> None:
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
        for line in stdout:
            logging.debug(f"> {line.rstrip()}")
            InstallationFailed.check(line, stdout)
            if RESTORING_BACKUP.search(line):
                break
        logging.info("Restoring backup started")

    # Increase timeout to 5 minutes for this section
    stdout.channel.settimeout(5 * 60)

    for line in stdout:
        logging.debug(f"> {line.rstrip()}")
        InstallationFailed.check(line, stdout)
        if DATA_RESTORATION_COMPLETE.search(line):
            break
    logging.info("Data restoration succeeded")

    # Plug screen (#TODO re-use older screen)
    screen = pyte.Screen(columns=80, lines=24)
    stream = pyte.ByteStream(screen)
    while installer.console_channel.recv_ready():
        stream.feed(installer.console_channel.recv(BUFFER_READ_SIZE))
    logging.debug(f"Restore completing screen:\n{show_screen(screen)}")

    # Wait for restore completed screen
    logging.info("Wait for restore completed screen")
    while not any("┤ Restore Complete ├" in line for line in screen.display):
        stream.feed(installer.console_channel.recv(BUFFER_READ_SIZE))

    # Wait for screen to stabilize
    time.sleep(1)
    while installer.console_channel.recv_ready():
        stream.feed(installer.console_channel.recv(BUFFER_READ_SIZE))
    logging.debug(f"Restore completed screen:\n{show_screen(screen)}")
    installer.console_channel.send(b"\r")

    # Decrease back to 1 minute
    stdout.channel.settimeout(60)

    for line in stdout:
        logging.debug(f"> {line.rstrip()}")
        InstallationFailed.check(line, stdout)
    logging.info("The installer process has terminated")
