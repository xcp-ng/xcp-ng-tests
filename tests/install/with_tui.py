from __future__ import annotations

import pytest

import hashlib
import logging
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import paramiko

from lib.common import Defer, wait_for
from lib.host import Host
from lib.vm import VM

from .test import helper_vm_with_plugged_disk

from typing import Generator

def sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


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
    stdout = channel.makefile('rb', -1)

    # Wait for grub to finish
    for line in stdout:
        if b"Booting `install'" in line:
            break

    # Wait for TUI to appear
    for line in stdout:
        if b"Welcome to XCP-ng" in line:
            logging.info(f"Entering TUI: {line}")
            break
        assert isinstance(line, bytes)
        if b"\x1b" in line:
            logging.info(f"! {line!r}")
        else:
            decoded = line.decode(errors="ignore").rstrip()
            logging.info(f"> {decoded}")

    # Maybe some data already got extracted in the stdout buffer
    extra_data = getattr(stdout, "_rbuffer")
    assert isinstance(extra_data, bytes)

    _select_keymap_dialog = wait_for_dialog(channel, b"Select Keymap")
    channel.send(b"\t\r")  # Validate US
    _welcome_dialog = wait_for_dialog(channel, b"Welcome to XCP-ng Setup")
    channel.send(b"\r")  # Do not reboot, continue
    _end_user_agreement_dialog = wait_for_dialog(channel, b"End User Agreement")
    channel.send(b"\t\r")  # Accept the end user agreement
    _select_primary_disk_dialog = wait_for_dialog(channel, b"Select Primary Disk")
    channel.send(b"\t\r")  # Select first disk
    _virtual_machine_storage_dialog = wait_for_dialog(channel, b"Virtual Machine Storage")
    channel.send(b"\t\r")  # Select first disk
    _virtual_machine_storage_type_dialog = wait_for_dialog(channel, b"Virtual Machine Storage Type")
    channel.send(b"\t\t\r")  # Select EXT
    _select_installation_source_dialog = wait_for_dialog(channel, b"Select Installation Source")
    channel.send(b"\t\r")  # Select Local Media
    _verify_installation_source_dialog = wait_for_dialog(channel, b"Verify Installation Source")
    channel.send(b"\x1b[A\t\r")  # Skip the verification
    _set_password_dialog = wait_for_dialog(channel, b"Set Password")
    channel.send(f"{HOST_DEFAULT_PASSWORD}\t{HOST_DEFAULT_PASSWORD}\t\r".encode())  # Type root password
    _networking_1_dialog = wait_for_dialog(channel, b"Networking")
    channel.send(b"\t\t\t\r")  # IPv4
    _networking_2_dialog = wait_for_dialog(channel, b"Networking")
    channel.send(b"\t\t\t\r")  # DHCP
    _hostname_and_dns_configuration_dialog = wait_for_dialog(channel, b"Hostname and DNS Configuration")
    channel.send(b"\t\t\t\t\t\r")  # Random hostname and DNS set by DHCP
    _select_time_zone_1_dialog = wait_for_dialog(channel, b"Select Time Zone")
    channel.send(b"\x1b[6~\r")  # Page down to select Europe
    _select_time_zone_2_dialog = wait_for_dialog(channel, b"Select Time Zone")
    channel.send(b"\x1b[6~\x1b[6~\x1b[6~\x1b[6~\r")  # 4 Page down to select Paris
    _system_time_dialog = wait_for_dialog(channel, b"System Time")
    channel.send(b"\t\r")
    _confirm_installation_dialog = wait_for_dialog(channel, b"Confirm Installation")
    channel.send(b"\t\r")

    channel.settimeout(600)
    _installation_complete_dialog = wait_for_dialog(channel, b"Installation Complete")


def wait_for_dialog(
    channel: paramiko.Channel, title: bytes,
    dialog: bytes = b"", delay: float = 1.0,
) -> bytes:
    logging.info(f"Wait for {title!r} dialog title")
    while title not in dialog:
        dialog += channel.recv(1024)
    logging.info(f"Wait for {title!r} dialog to stabilize")
    time.sleep(delay)
    while channel.recv_ready():
        dialog += channel.recv(1024)
    return dialog

def show_dialog(dialog: bytes) -> None:
    """Helper to use when debugging the dialogs"""
    print("\x1b[2J\x1b[H" + dialog.decode() + "\x1b[24H\n")


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
