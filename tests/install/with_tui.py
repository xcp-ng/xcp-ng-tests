from __future__ import annotations

import pytest

import hashlib
import logging
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import paramiko
import pyte

from lib.commands import ssh
from lib.common import Defer, wait_for
from lib.host import Host
from lib.vm import VM

from .test import helper_vm_with_plugged_disk

from typing import Generator

BUFFER_READ_SIZE = 8192

def sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()

def scan_for_mac_address(address: str, ip_range: str = "10.1.1-9.0-255") -> str | None:
    from data import ARP_SERVER
    awk = f"/Nmap scan report for/ {{ip=$5}} tolower($0) ~ /{address}/ {{print ip}}"
    command = f"nmap -sn {ip_range} | awk '{awk}'"
    output = ssh(ARP_SERVER, command).strip()
    return output or None

def vm_definition(firmware: str) -> dict:
    from data import NETWORKS

    return dict(
        name="vm1",
        template="Other install media",
        params=(
            # dict(param_name="", value=""),
            dict(param_name="memory-static-max", value="4GiB"),
            dict(param_name="memory-dynamic-max", value="4GiB"),
            dict(param_name="memory-dynamic-min", value="4GiB"),
            dict(param_name="VCPUs-max", value="2"),
            dict(param_name="VCPUs-at-startup", value="2"),
            dict(param_name="platform", key="exp-nested-hvm", value="true"), # FIXME < 8.3 host?
            dict(param_name="platform", key="nested-virt", value="true"), # FIXME >= 8.3 host?
            dict(param_name="HVM-boot-params", key="order", value="dc"),
        ) + {
            "uefi": (
                dict(param_name="HVM-boot-params", key="firmware", value="uefi"),
                dict(param_name="platform", key="device-model", value="qemu-upstream-uefi"),
            ),
            "bios": (),
        }[firmware],
        vdis=[
            dict(name="vm1 system disk", size="100GiB", device="xvda", userdevice="0"),
            dict(name="vm1 extra disk", size="50GiB", device="xvdb", userdevice="1")
        ],
        cd_vbd=dict(device="xvdd", userdevice="3"),
        vifs=[dict(index=0, network_name=NETWORKS["MGMT"])],
    )

@pytest.fixture(scope='function')
def remote_installer_iso(host: Host, installer_iso: dict[str, str | bool]) -> Generator[str]:
    from data import OBJECTS_NAME_PREFIX

    assert isinstance(installer_iso['iso'], str)
    base_iso_file = Path(installer_iso['iso'])
    base_iso_name = base_iso_file.stem
    base_iso_hash = sha256(base_iso_file)[:8]
    base_iso_key = f"{base_iso_name}-{base_iso_hash}"
    remote_filename = f"{OBJECTS_NAME_PREFIX}tests-install-cache-{base_iso_key}.iso"

    iso_sr = host.pool.get_iso_sr()
    if not host.xe(
        "vdi-list", {"sr-uuid": iso_sr.uuid, "name-label": remote_filename},
        minimal=True,
    ):
        mountpoint = f"/run/sr-mount/{iso_sr.uuid}"
        destination = f"{mountpoint}/{remote_filename}"
        host.pool.push_iso(str(base_iso_file), destination)

    yield remote_filename

@pytest.fixture
def vm_booted_with_original_installer(
    host: Host, create_vms: list[VM], remote_installer_iso: str
) -> Generator[VM, None, None]:

    host_vm, = create_vms # one single VM

    vif = host_vm.vifs()[0]
    mac_address = vif.param_get('MAC')
    assert mac_address is not None
    logging.info("Host VM has MAC %s", mac_address)

    host_vm.insert_cd(remote_installer_iso)
    host_vm.start()
    wait_for(host_vm.is_running, "Wait for host VM running")

    yield host_vm

    logging.info("Shutting down Host VM")
    host_vm.shutdown(force=True)

    host_vm.eject_cd()

@pytest.mark.dependency()
@pytest.mark.parametrize("local_sr", ("ext",)) # TODO: "nosr" and "lvm"
@pytest.mark.parametrize("package_source", ("iso",)) # TODO: "net"
@pytest.mark.parametrize("iso_version", ("83nightly",)) # TODO: support other ISOs?
@pytest.mark.parametrize("firmware", ("uefi",)) # TODO: "bios"
@pytest.mark.vm_definitions(lambda firmware: vm_definition(firmware))
def test_install_with_tui(
    vm_booted_with_original_installer: VM,
    firmware: str, iso_version: str, package_source: str, local_sr: str,
    defer: Defer,
):
    from data import HOST_DEFAULT_PASSWORD

    vm = vm_booted_with_original_installer
    vif = vm.vifs()[0]
    mac_address = vif.param_get('MAC')
    assert mac_address is not None
    residence_host = vm.get_residence_host()
    dom_id = residence_host.xe(
        'vm-param-get',
        {'uuid': vm.uuid, 'param-name': 'dom-id'},
    )

    class IgnorePolicy(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            pass

    client = paramiko.SSHClient()
    defer(client.close)
    client.set_missing_host_key_policy(IgnorePolicy())
    logging.info(f"Connecting to {residence_host.hostname_or_ip}")
    client.connect(residence_host.hostname_or_ip, username='root')
    transport = client.get_transport()
    assert transport is not None
    channel = transport.open_session()
    channel.get_pty(term='vt100', width=80, height=24)
    command = f"xl console -t serial {dom_id}"
    logging.info(f"Connecting to serial line with {command!r}")
    channel.exec_command(command.encode())
    channel.settimeout(30.0)

    grub_screen = pyte.Screen(columns=100, lines=32)
    grub_screen.define_charset("U", "(")
    grub_stream = pyte.ByteStream(grub_screen)
    grub_stream.select_other_charset("@")

    # Wait for grub to appear
    while not any("`e' to edit the commands" in line for line in grub_screen.display):
        grub_stream.feed(channel.recv(BUFFER_READ_SIZE))

    # Wait for grub to stabilize
    time.sleep(1)
    while channel.recv_ready():
        grub_stream.feed(channel.recv(BUFFER_READ_SIZE))
    logging.info(f"Grub screen:\n{show_screen(grub_screen)}")

    # Send:
    # - ctrl-n (x3): Next line
    # - ctrl-e: End of line
    # - space + vmlinuz extra config
    vmlinuz_extra_config = f"network_device=all sshpassword={HOST_DEFAULT_PASSWORD}".encode()
    for char in b"e\x0e\x0e\x0e\x05 " + vmlinuz_extra_config:
        channel.send(bytes([char]))
        time.sleep(0.1)

    # Wait for grub to stabilize
    time.sleep(1)
    while channel.recv_ready():
        grub_stream.feed(channel.recv(BUFFER_READ_SIZE))
    logging.info(f"Grub screen after edition:\n{show_screen(grub_screen)}")

    # Send ctrl-x: Save and boot
    channel.send(b"\x18")

    # Initialize new screen
    screen = pyte.Screen(columns=80, lines=24)
    stream = pyte.ByteStream(screen)

    # Wait for TUI to appear
    stdout = channel.makefile('rb', -1)
    for line in stdout:
        assert isinstance(line, bytes)

        # Start feeding the terminal emulator from here
        stream.feed(line)

        if b"Welcome to XCP-ng" in line:
            logging.info(f"Entering TUI: {line!r}")
            break
        elif b"\x1b" in line:
            logging.info(f"! {line!r}")
        else:
            decoded = line.decode(errors="ignore").rstrip()
            logging.info(f"> {decoded}")

    # Maybe some data already got extracted in the stdout buffer
    extra_data = getattr(stdout, "_rbuffer")
    assert isinstance(extra_data, bytes)
    stream.feed(extra_data)

    def wait_for_dialog(
        title: str,
        discriminant: str = "",
        delay: float = 1.0,
    ) -> None:
        wrapped_title = f"─┤ {title} ├─"
        logging.info(f"Wait for {title!r} dialog title")
        while not (
            any(wrapped_title in line for line in screen.display)
            and any(discriminant in line for line in screen.display)
        ):
            stream.feed(channel.recv(BUFFER_READ_SIZE))
        logging.info(f"Wait for {title!r} dialog to stabilize")
        time.sleep(delay)
        while channel.recv_ready():
            stream.feed(channel.recv(BUFFER_READ_SIZE))
        logging.info(f"{title!r} dialog reached\n{show_screen(screen)}")

    def send_tab(n: int = 1, delay: float = 1.0) -> None:
        channel.send(b"\t" * n)
        time.sleep(delay)
        while channel.recv_ready():
            stream.feed(channel.recv(BUFFER_READ_SIZE))
        logging.info(f"Selection updated with {n} tab(s)\n{show_screen(screen)}")

    def send_up(n: int = 1, delay: float = 1.0) -> None:
        channel.send(b"\x1b[A" * n)
        time.sleep(delay)
        while channel.recv_ready():
            stream.feed(channel.recv(BUFFER_READ_SIZE))
        logging.info(f"Selection updated with {n} up(s)\n{show_screen(screen)}")

    def send_password(n: int = 1, delay: float = 1.0) -> None:
        channel.send(f"{HOST_DEFAULT_PASSWORD}\t".encode() * 2)
        time.sleep(delay)
        while channel.recv_ready():
            stream.feed(channel.recv(BUFFER_READ_SIZE))
        logging.info(f"Selection updated with {n} password(s)\n{show_screen(screen)}")

    def send_pagedown(n: int = 1, delay: float = 1.0) -> None:
        channel.send(b"\x1b[6~" * n)
        time.sleep(delay)
        while channel.recv_ready():
            stream.feed(channel.recv(BUFFER_READ_SIZE))
        logging.info(f"Selection updated with {n} pagedown(s)\n{show_screen(screen)}")

    def validate(expected_selection: str | None = None, expected_validation: str = "Ok") -> None:
        highlighted = get_highlighted(screen)
        if expected_selection is None:
            validation, = highlighted
        else:
            selected, validation = highlighted
            assert expected_selection in selected
        assert validation == f" {expected_validation} "
        if expected_selection is None:
            logging.info(f"Validate dialog using {expected_validation!r}")
        else:
            logging.info(f"Validate {expected_selection!r} selection using {expected_validation!r}")
        channel.send(b"\r")

    wait_for_dialog("Select Keymap")
    send_tab()
    validate(expected_selection="[qwerty] us")

    # The host should soon get an IP
    retries = 3
    for i in range(retries):
        ip = scan_for_mac_address(mac_address)
        if ip is not None:
            vm.ip = ip
            logging.info(f"VM IP: {ip}")
            break
        logging.info(f"Could not get an IP address on attempt {i}")

    wait_for_dialog("Welcome to XCP-ng Setup")
    validate()

    wait_for_dialog("End User Agreement")
    send_tab()
    validate(expected_validation="Accept EUA")

    wait_for_dialog("Select Primary Disk")
    send_tab()
    validate(expected_selection="nvme0n1")

    wait_for_dialog("Virtual Machine Storage")
    send_tab()
    validate(expected_selection="nvme0n1")

    wait_for_dialog("Virtual Machine Storage Type")
    send_tab(n=2)
    validate()

    wait_for_dialog("Select Installation Source")
    send_tab()
    validate(expected_selection="Local media")

    wait_for_dialog("Verify Installation Source")
    send_up()
    send_tab()
    validate(expected_selection="Skip verification")

    wait_for_dialog("Set Password")
    send_password(n=2)
    validate()

    wait_for_dialog("Networking", discriminant="IPv4")
    send_tab(n=3)
    validate()

    wait_for_dialog("Networking", discriminant="DHCP")
    send_tab(n=3)
    validate()

    wait_for_dialog("Hostname and DNS Configuration")
    send_tab(n=5)
    validate()

    wait_for_dialog("Select Time Zone", discriminant="Africa")
    send_pagedown()
    send_tab()
    validate(expected_selection="Europe")

    wait_for_dialog("Select Time Zone", discriminant="Amsterdam")
    send_pagedown(n=4)
    send_tab()
    validate(expected_selection="Paris")

    wait_for_dialog("System Time")
    send_tab()
    validate(expected_selection="Use DHCP NTP servers")

    wait_for_dialog("Confirm Installation")
    send_tab()
    validate(expected_validation="Install XCP-ng")

    channel.settimeout(600)
    wait_for_dialog("Installation Complete")

def show_screen(screen: pyte.Screen) -> str:
    ANSI_RESET = "\033[0m"
    ANSI_BOLD = "\033[1m"
    ANSI_REVERSE = "\033[7m"

    columns = screen.columns

    # 1. Draw the top border
    result = ["┌" + "─" * columns + "┐"]

    # 2. Draw each row with left and right borders
    for row_idx in range(screen.lines):
        row = screen.buffer[row_idx]

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

def get_highlighted(screen: pyte.Screen) -> list[str]:
    highlighted = []
    for i in range(screen.lines):
        row = screen.buffer[i]
        previously_reversed = False
        for j in range(screen.columns):
            char = row[j]
            if char.reverse and not previously_reversed:
                highlighted.append("")
            if char.reverse:
                highlighted[-1] += char.data
            previously_reversed = char.reverse
    return highlighted


@pytest.mark.dependency()
@pytest.mark.usefixtures("xcpng_chained")
@pytest.mark.parametrize("local_sr", ("ext",))
@pytest.mark.parametrize("package_source", ("iso",))
@pytest.mark.parametrize("machine", ("host1", "host2"))
@pytest.mark.parametrize("version", ("83nightly",))
@pytest.mark.parametrize("firmware", ("uefi",))
@pytest.mark.continuation_of.with_args(
    lambda version, firmware, local_sr, package_source: [dict(
        vm="vm1",
        image_test=f"test_install_with_tui[{firmware}-{version}-{package_source}-{local_sr}]")])
@pytest.mark.small_vm
def test_tune_firstboot(create_vms: list[VM], helper_vm_with_plugged_disk: VM,
                        firmware: str, version: str, machine: str, local_sr: str, package_source: str) -> None:
    from data import TEST_SSH_PUBKEY

    helper_vm = helper_vm_with_plugged_disk

    helper_vm.ssh("mount /dev/xvdb1 /mnt")
    try:
        # hostname
        logging.info("Setting hostname to %r", machine)
        helper_vm.ssh(f'echo {machine} > /mnt/etc/hostname')
        # UUIDs
        logging.info("Randomizing UUIDs")
        helper_vm.ssh(
            f'''sed -i -e "/^INSTALLATION_UUID=/ s/.*/INSTALLATION_UUID='{uuid4()}'/" -e "/^CONTROL_DOMAIN_UUID=/ s/.*/CONTROL_DOMAIN_UUID='{uuid4()}'/" /mnt/etc/xensource-inventory''' # noqa
        )
        helper_vm.ssh("grep UUID /mnt/etc/xensource-inventory")
        logging.info("Add the CI SSH key")
        helper_vm.ssh(f'echo "{TEST_SSH_PUBKEY}" >> /mnt/root/.ssh/authorized_keys')
        logging.info("Configure the test-pingpxe service")
        configure_pingpxe_service(helper_vm)
    finally:
        helper_vm.ssh("umount /dev/xvdb1")


def configure_pingpxe_service(helper_vm: VM):
    from data import ARP_SERVER

    # Copy test-pingpxe script
    pingpxe_path = Path(__file__).parent / "test-pingpxe.sh"
    assert pingpxe_path.exists()
    helper_vm.scp(str(pingpxe_path.absolute()), "/mnt/usr/local/sbin/test-pingpxe.sh")

    # Copy test-pingpxe service
    service_destination = "/mnt/etc/systemd/system/test-pingpxe.service"
    with tempfile.NamedTemporaryFile("w") as f:
        f.write(f"""\
[Unit]
Description=Ping pxe server to populate its ARP table
After=network-online.target
[Service]
Type=oneshot
ExecStart=/bin/sh -c 'while ! /usr/local/sbin/test-pingpxe.sh "{ARP_SERVER}"; do sleep 1 ; done'
[Install]
WantedBy=default.target
""")
        f.flush()
        helper_vm.scp(f.name, service_destination)

    # Enable test-pingpxe service
    helper_vm.ssh(
        f"ln -s {service_destination} /mnt/etc/systemd/system/default.target.wants/test-pingpxe.service"
    )


@pytest.mark.dependency()
@pytest.mark.usefixtures("xcpng_chained")
@pytest.mark.parametrize("local_sr", ("ext",))
@pytest.mark.parametrize("package_source", ("iso",))
@pytest.mark.parametrize("machine", ("host1", "host2"))
@pytest.mark.parametrize("version", ("83nightly",))
@pytest.mark.parametrize("firmware", ("uefi",))
@pytest.mark.continuation_of.with_args(
    lambda firmware, version, machine, local_sr, package_source: [
        dict(vm="vm1",
                image_test=("test_tune_firstboot"
                            f"[None-{firmware}-{version}-{machine}-{package_source}-{local_sr}]"))])
def test_boot_inst(create_vms: list[VM],
                   firmware: str, version: str, machine: str, package_source: str, local_sr: str) -> None:
    from .test import TestNested

    test_firstboot = getattr(TestNested(), "_test_firstboot")
    test_firstboot(create_vms, version, machine=machine)
