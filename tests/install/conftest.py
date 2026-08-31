from __future__ import annotations

import pytest
import pytest_dependency  # type: ignore[import-untyped]

import hashlib
import logging
import os
import tempfile
import time
import urllib.parse
from pathlib import Path

import paramiko

from data import (
    HOST_DEFAULT_PASSWORD,
    ISO_IMAGES,
    ISO_IMAGES_BASE,
    ISO_IMAGES_CACHE,
    PXE_CONFIG_SERVER,
    TEST_SSH_PUBKEY,
    TOOLS,
)
from lib import installer, pxe
from lib.commands import local_cmd, scp, ssh
from lib.common import Defer, callable_marker, url_download, wait_for
from lib.installer import AnswerFile

from .boot import customize_grub, customize_isolinux
from .postinstall import make_postinstall_script

from typing import TYPE_CHECKING, Iterator, Sequence

if TYPE_CHECKING:
    from lib.host import Host
    from lib.vm import VM


def sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def skip_package_source(version: str, package_source: str) -> tuple[bool, str]:
    """
    Return true if the version of the ISO doesn't support the source type.
    Note: this is a quick-win hack, to avoid explicit enumeration of supported
    package_source values for each ISO.
    """
    if version not in ISO_IMAGES:
        return True, "version of ISO {} is unknown".format(version)

    if package_source == "iso":
        if ISO_IMAGES[version].get('net-only', False):
            return True, "ISO image is net-only while package_source is local"

        return False, "do not skip"

    if package_source == "net":
        # Net install is not valid if there is no netinstall URL
        # FIXME: ISO includes a default URL so we should be able to omit net-url
        if 'net-url' not in ISO_IMAGES[version]:
            return True, "net-url required for netinstall was not found for {}".format(version)

        return False, "do not skip"

    # If we don't know the source type then it is invalid
    return True, "unknown source type {}".format(package_source)


@pytest.fixture(scope='function')
def answerfile(request: pytest.FixtureRequest) -> AnswerFile | None:
    """
    Makes an AnswerFile object available to test and other fixtures.

    AnswerFile object are typically generated from a template
    customizable in `data.py` specified to the ctor, and extended by:
    - adding attributes to the top element
    - appending new elements to the top element's children

    > @pytest.mark.answerfile(lambda firmware: AnswerFile("INSTALL")
    >                         .top_setattr({"sr-type": local_sr})
    >                         .top_append(
    >                             {"TAG": "source", "type": "local"},
    >                             {"TAG": "primary-disk",
    >                              "guest-storage": "yes",
    >                              "CONTENTS": {"uefi": "nvme0n1", "bios": "sda"}[firmware]},
    >                         ))
    > def test_install(answerfile):
    >     answerfile.write_xml("my-answers.xml")
    """
    marker = request.node.get_closest_marker("answerfile")

    if marker is None:
        return None

    # construct answerfile definition from option "base", and explicit bits
    answerfile_def: AnswerFile = callable_marker(marker.args[0], request)
    assert isinstance(answerfile_def, AnswerFile)

    return answerfile_def


@pytest.fixture
def uploaded_postinstall():
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / "postinstall.sh"
        logging.info(f"Generating postinstall script in {local_path}")
        local_path.write_text(make_postinstall_script())

        # Copy the file on the PXE/ARP server
        postinstall_hash = sha256(local_path)
        postinstall_name = f"postinstall.{postinstall_hash[:16]}.sh"
        pxe_destination = Path("/pxe/configs/ci/") / postinstall_name
        logging.info(f"Copying postinstall script to {PXE_CONFIG_SERVER}:{pxe_destination}")
        scp(PXE_CONFIG_SERVER, str(local_path), str(pxe_destination))

        # Check that the file is avaialble through HTTP
        postinstall_url = f"http://{PXE_CONFIG_SERVER}/configs/ci/{postinstall_name}"
        local_copy = Path(tmpdir) / "postinstall.copy.sh"
        logging.info(f"Checking that the postinstall script is available through {postinstall_url}")
        url_download(postinstall_url, str(local_copy))
        assert local_path.read_bytes() == local_copy.read_bytes()

        return postinstall_url


@pytest.fixture
def uploaded_answerfile(
    answerfile: AnswerFile | None,
    installer_iso: tuple[str, bool],
    uploaded_postinstall: str,
) -> str:
    _, unsigned = installer_iso
    assert answerfile is not None

    # Customize the answerfile
    answerfile.top_append({
        "TAG": "script",
        "stage": "filesystem-populated",
        "type": "url",
        "CONTENTS": uploaded_postinstall,
    })
    if unsigned:
        # Use both the *gpgcheck 8.3+ syntax and the netinstall-gpg-check 8.2 syntax,
        # as installers ignore attributes they don't know
        answerfile.top_setattr(
            {'gpgcheck': "false", 'repo-gpgcheck': "false", 'netinstall-gpg-check': "false"}
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write the answerfile to a temproary file
        local_path = Path(tmpdir) / "answerfile.xml"
        logging.info(f"Generating answerfile in {local_path}")
        answerfile.write_xml(str(local_path))

        # Copy the file on the PXE/ARP server
        answerfile_hash = sha256(local_path)
        answerfile_name = f"answerfile.{answerfile_hash[:16]}.xml"
        pxe_destination = Path("/pxe/configs/ci/") / answerfile_name
        logging.info(f"Copying answerfile to {PXE_CONFIG_SERVER}:{pxe_destination}")
        scp(PXE_CONFIG_SERVER, str(local_path), str(pxe_destination))

        # Check that the file is avaialble through HTTP
        answerfile_url = f"http://{PXE_CONFIG_SERVER}/configs/ci/{answerfile_name}"
        local_copy = Path(tmpdir) / "answerfile.copy.xml"
        logging.info(f"Checking that the answerfile is available through {answerfile_url}")
        url_download(answerfile_url, str(local_copy))
        assert local_path.read_bytes() == local_copy.read_bytes()

    return answerfile_url


@pytest.fixture
def vmlinuz_config(uploaded_answerfile: str) -> str:
    assert " " not in uploaded_answerfile
    vmlinuz_config = "/boot/vmlinuz install"
    vmlinuz_config += " console=tty0"
    vmlinuz_config += " network_device=all"
    vmlinuz_config += f" sshpassword={HOST_DEFAULT_PASSWORD}"
    vmlinuz_config += f" answerfile={uploaded_answerfile}"
    vmlinuz_config += " atexit=shell"
    return vmlinuz_config


@pytest.fixture
def installer_iso(host: Host, request: pytest.FixtureRequest) -> tuple[str, bool]:
    iso_key = request.getfixturevalue("iso_version")
    package_source = request.getfixturevalue("package_source")
    skip, reason = skip_package_source(iso_key, package_source)
    if skip:
        pytest.skip(reason)
    assert iso_key in ISO_IMAGES, f"ISO_IMAGES does not have a value for {iso_key}"
    iso = ISO_IMAGES[iso_key]['path']
    unsigned = ISO_IMAGES[iso_key].get('unsigned', False)
    return iso, unsigned


@pytest.fixture(scope='function')
def uploaded_iso(host: Host, installer_iso: tuple[str, bool]) -> str:
    iso, _ = installer_iso
    iso_url: str | None = None
    iso_local_path: Path | None = None

    # ISO is provided as a URL
    if iso.startswith("http://"):
        iso_url = iso
        *_, iso_name = urllib.parse.urlsplit(iso).path.split("/")
    # ISO is provided as an absolute path
    elif iso.startswith("/"):
        iso_local_path = Path(iso)
        assert iso_local_path.exists(), f"File {iso} does not exist"
        iso_name = iso_local_path.name
    # ISO provided as a name
    elif "/" not in iso:
        iso_name = iso
    else:
        assert False, f"{iso!r} not supported (must be a name, an HTTP URL or an absolute path)"

    # Special handling of `-latest` names, that we want to resolve as early as possible
    if iso_name.endswith("-latest"):
        symlink = f"/pxe/isos/{iso_name}"
        iso_name = Path(ssh(PXE_CONFIG_SERVER, f"readlink {symlink!r}")).name

    # Compute URL and local path from actual ISO name
    if iso_url is None:
        iso_url = urllib.parse.urljoin(ISO_IMAGES_BASE, iso_name)
    if iso_local_path is None:
        iso_local_path = Path(ISO_IMAGES_CACHE) / iso_name

    # Get ISO SR information
    iso_sr = host.pool.get_iso_sr()
    mountpoint = Path("/run/sr-mount") / iso_sr.uuid
    destination = mountpoint / iso_name
    assert destination.suffix == ".iso"

    # The name already exists in the ISO SR
    if host.xe(
        "vdi-list",
        {"sr-uuid": iso_sr.uuid, "name-label": destination.name},
        minimal=True,
    ):
        # If the image is available locally, make sure the files are identical
        if iso_local_path.exists():
            iso_hash = sha256(iso_local_path)
            remote_hash, *_ = host.pool.master.ssh(f"sha256sum {destination}").split()
            assert iso_hash == remote_hash, f"remote and local files do not match for {iso_name!r}"

        return destination.name

    # The ISO must first be downloaded
    if not iso_local_path.exists():
        logging.info(f"Downloading {iso_url!r} into {iso_local_path}")
        url_download(iso_url, str(iso_local_path))

    # Then upload the ISO file
    host.pool.push_iso(str(iso_local_path), str(destination))
    return destination.name


@pytest.fixture
def maybe_remastered_iso(host: Host, create_vms: list[VM], uploaded_iso: str) -> str:
    # Remastering is only needed with BIOS firmware
    (host_vm,) = create_vms
    if host_vm.is_uefi:
        return uploaded_iso

    # Remastering is only needed with XenServer 7 and XCP-ng 7
    iso_name = uploaded_iso
    if not any(iso_name.lower().startswith(prefix) for prefix in ("xenserver-7", "xcp-ng-7")):
        return uploaded_iso

    # Make sure the `iso-remaster` tool is available
    iso_remaster = TOOLS["iso-remaster"]
    assert os.access(iso_remaster, os.X_OK)

    # Prepare paths
    iso_sr = host.pool.get_iso_sr()
    iso_local_path = Path(ISO_IMAGES_CACHE) / iso_name
    iso_remote_path = Path("/run/sr-mount") / iso_sr.uuid / iso_name
    remastered_iso_remote_path = iso_remote_path.with_suffix(".serialfix.iso")

    # Download the ISO if it's not already in the cache
    if not iso_local_path.exists():
        logging.info(f"Copying {iso_name} from SR {iso_sr.uuid} into {iso_local_path}")
        host.pool.master.scp(str(iso_remote_path), str(iso_local_path), local_dest=True)

    # Work in a temporary directory
    with tempfile.TemporaryDirectory(prefix="remastered-iso-") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        remastered_iso = temp_dir / "remastered.iso"
        iso_patcher_script = Path(__file__).parent / "iso_patcher_bios_serial_fix.sh"

        # Run the remastering script
        logging.info(f"Remastering ISO in {iso_local_path} to {remastered_iso}")
        local_cmd(
            [
                iso_remaster,
                "--iso-patcher",
                str(iso_patcher_script),
                str(iso_local_path),
                str(remastered_iso),
            ],
            cwd=temp_dir,
        )

        # Upload the ISO
        logging.info(f"Copying the remastered iso to SR {remastered_iso_remote_path}")
        host.pool.push_iso(str(remastered_iso), str(remastered_iso_remote_path))

        return remastered_iso_remote_path.name

@pytest.fixture(scope='function')
def system_disks_names(request: pytest.FixtureRequest) -> tuple[str, ...]:
    firmware = request.getfixturevalue("firmware")
    main_disk = {"uefi": "nvme0n1", "bios": "sda"}[firmware]
    return (main_disk,)


@pytest.fixture
def unplug_second_disk_during_restore(
    host: Host, create_vms: list[VM], request: pytest.FixtureRequest
) -> Iterator[str | None]:
    """
    This fixture is a workaround to a bug in udev: when a controller exposes
    two disks (as it is the case for the nested hosts used in the automated
    installation tests), udev exposes an incorrect symlink in `/dev/disk/by-id`.
    This causes the restore operation to crash with an error.

    This bug should be fixed in systemd 219-57.5.1
    See the corresponding PR: https://github.com/xcp-ng-rpms/systemd/pull/2
    """
    # Only apply this fix in `test_restore`
    if request.node.originalname != "test_restore":
        yield None
        return

    # Retrieve VBD and VDI UUDI
    (host_vm,) = create_vms
    vbd_uuid = host.xe(
        "vbd-list", args={"vm-uuid": host_vm.uuid, "userdevice": "1", "params": "uuid"}, minimal=True
    )
    vdi_uuid = host.xe(
        "vbd-list", args={"vm-uuid": host_vm.uuid, "userdevice": "1", "params": "vdi-uuid"}, minimal=True
    )

    # Do nothing if there is only one disk configured
    if not vbd_uuid:
        yield None
        return

    # Destroy the VBD (since there's no way to start the VM with the VBD unplugged)
    logging.info(f"Destroying VBD {vbd_uuid} binding VDI {vdi_uuid} on {host_vm.uuid}")
    host.xe("vbd-destroy", args={"uuid": vbd_uuid})
    yield vdi_uuid

    # Recreate the destroyed VBD
    logging.info(f"Restoring VDI {vdi_uuid} on {host_vm.uuid}")
    host.xe("vbd-create", args={"vm-uuid": host_vm.uuid, "vdi-uuid": vdi_uuid, "device": "1"})


@pytest.fixture(scope='function')
def vm_booted_with_installer(
    host: Host,
    create_vms: list[VM],
    maybe_remastered_iso: str,
    defer: Defer,
    vmlinuz_config: str,
    unplug_second_disk_during_restore: str | None,
) -> Iterator[VM]:
    # Get host mac address
    (host_vm,) = create_vms
    vif = host_vm.vifs()[0]
    mac_address = vif.param_get('MAC')
    assert mac_address is not None
    logging.info("Host VM has MAC %s", mac_address)

    # Start the host VM
    host_vm.insert_cd(maybe_remastered_iso)
    host_vm.start()

    # Get the domain corresponding to the host VM
    residence_host = host_vm.get_residence_host()
    dom_id = residence_host.xe(
        'vm-param-get',
        {'uuid': host_vm.uuid, 'param-name': 'dom-id'},
    )

    # Prepare an SSH connection to the residence host
    class IgnorePolicy(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            pass

    residence_client = paramiko.SSHClient()
    logging.info(f"Open an SSH channel to {residence_host.hostname_or_ip}")
    residence_client.set_missing_host_key_policy(IgnorePolicy())
    residence_client.connect(residence_host.hostname_or_ip, username='root')
    residence_transport = residence_client.get_transport()
    assert residence_transport is not None
    residence_channel = residence_transport.open_session()

    # Defer cleanup to allow for easier debugging with `--pdb`
    def cleanup():
        residence_client.close()
        host_vm.eject_cd()
        if not host_vm.is_halted():
            host_vm.shutdown(force=True)

    defer(cleanup)

    # Intercept grub or isolinux to provide a custom vmlinuz configuration
    wait_for(host_vm.is_running, "Wait for host VM running")
    if host_vm.is_uefi:
        logging.info(f"Accessing grub using serial line in domain {dom_id} on {residence_host}")
        customize_grub(
            residence_channel,
            dom_id,
            vmlinuz_config,
        )
    else:
        logging.info(f"Accessing isolinux using serial line in domain {dom_id} on {residence_host}")
        customize_isolinux(
            residence_channel,
            dom_id,
            vmlinuz_config,
        )

    # The channel on residence host has served its purpose
    residence_channel.close()

    # Wait for IP address to appear in the PXE AEP table
    wait_for(
        lambda: pxe.arp_addresses_for(mac_address),
        "Wait for DHCP server to see Host VM in ARP tables",
        timeout_secs=10 * 60,
    )
    (host_vm_ip,) = pxe.arp_addresses_for(mac_address)
    logging.info(f"Host VM has IP {host_vm_ip}")
    host_vm.ip = host_vm_ip

    # Wait for SSH server to be ready
    wait_for(
        lambda: local_cmd(["nc", "-zw5", host_vm_ip, "22"], check=False).returncode == 0,
        "Wait for ssh up on host",
        timeout_secs=10 * 60,
        retry_delay_secs=5,
    )

    # Add CI key
    logging.info(f"Add CI keys to {host_vm.ip}")
    with paramiko.SSHClient() as client:
        client.set_missing_host_key_policy(IgnorePolicy())
        client.connect(host_vm.ip, username='root', password=HOST_DEFAULT_PASSWORD)
        stdin, stdout, stderr = client.exec_command(
            f'mkdir /root/.ssh && echo "{TEST_SSH_PUBKEY}" > /root/.ssh/authorized_keys'
        )
        exit_status = stdout.channel.recv_exit_status()
        assert exit_status == 0

    yield host_vm

    logging.info("Shutting down Host VM")
    assert host_vm.ip is not None
    installer.poweroff(host_vm.ip)
    wait_for(host_vm.is_halted, "Wait for host VM halted")


@pytest.fixture(scope='function')
def xcpng_chained(request: pytest.FixtureRequest) -> None:
    # take test name from mark
    marker = request.node.get_closest_marker("continuation_of")
    assert marker is not None, "xcpng_chained fixture requires 'continuation_of' marker"
    continuation_of = callable_marker(marker.args[0], request)
    assert isinstance(continuation_of, Sequence)

    vm_defs = [
        dict(
            name=vm_spec['vm'],
            image_test=vm_spec['image_test'],
            image_vm=vm_spec.get("image_vm", vm_spec['vm']),
            image_scope=vm_spec.get("scope", "module"),
        )
        for vm_spec in continuation_of
    ]

    depends = [vm_spec['image_test'] for vm_spec in continuation_of]
    pytest_dependency.depends(request, depends)
    request.applymarker(pytest.mark.vm_definitions(*vm_defs))
