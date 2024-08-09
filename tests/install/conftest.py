import logging
import os
import pytest
import tempfile

from lib import installer, pxe
from lib.common import callable_marker, wait_for
from lib.commands import local_cmd, scp, ssh

from data import ANSWERFILE_URL, ISO_IMAGES, ISOSR_SRV, ISOSR_PATH, TEST_SSH_PUBKEY, TOOLS

@pytest.fixture(scope='function')
def installer_iso(request):
    marker = request.node.get_closest_marker("installer_iso")
    assert marker is not None, "installer_iso fixture requires 'installer_iso' marker"
    param_mapping = marker.kwargs.get("param_mapping", {})
    iso_key, = marker.args      # supports exactly one ISO
    iso_key = callable_marker(iso_key, request, param_mapping=param_mapping)

    assert iso_key in ISO_IMAGES, f"ISO_IMAGES does not have a value for {iso_key}"
    iso = ISO_IMAGES[iso_key]['path']
    logging.info("installer_iso: using %r", iso)
    return iso

@pytest.fixture(scope='function')
def remastered_iso(request, installer_iso):
    assert "iso-remaster" in TOOLS
    iso_remaster = TOOLS["iso-remaster"]
    assert os.access(iso_remaster, os.X_OK)

    with tempfile.TemporaryDirectory() as isotmp:
        remastered_iso = os.path.join(isotmp, "image.iso")
        img_patcher_script = os.path.join(isotmp, "img-patcher")
        iso_patcher_script = os.path.join(isotmp, "iso-patcher")

        logging.info("Remastering %s to %s", installer_iso, remastered_iso)

        # generate install.img-patcher script
        with open(img_patcher_script, "xt") as patcher_fd:
            print(f"""#!/bin/bash
set -ex
INSTALLIMG="$1"

mkdir -p "$INSTALLIMG/root/.ssh"
echo "{TEST_SSH_PUBKEY}" > "$INSTALLIMG/root/.ssh/authorized_keys"

cat > "$INSTALLIMG/root/postinstall.sh" <<EOF
#!/bin/sh
set -ex

ROOT="\\$1"

mkdir -p "\\$ROOT/root/.ssh"
echo "{TEST_SSH_PUBKEY}" >> "\\$ROOT/root/.ssh/authorized_keys"
EOF
""",
                  file=patcher_fd)
            os.chmod(patcher_fd.fileno(), 0o755)

        # generate iso-patcher script
        with open(iso_patcher_script, "xt") as patcher_fd:
            passwd = "passw0rd" # FIXME use invalid hash?
            print(f"""#!/bin/bash
set -ex
ISODIR="$1"
SED_COMMANDS=(-e "s@/vmlinuz@/vmlinuz sshpassword={passwd} atexit=shell@")
SED_COMMANDS+=(-e "s@/vmlinuz@/vmlinuz install answerfile={ANSWERFILE_URL} network_device=all@")

sed -i "${{SED_COMMANDS[@]}}" \
    "$ISODIR"/*/*/grub*.cfg \
    "$ISODIR"/boot/isolinux/isolinux.cfg
""",
                  file=patcher_fd)
            os.chmod(patcher_fd.fileno(), 0o755)

        # do remaster
        local_cmd([iso_remaster,
                   "--install-patcher", img_patcher_script,
                   "--iso-patcher", iso_patcher_script,
                   installer_iso, remastered_iso
                   ])

        yield remastered_iso

@pytest.fixture(scope='function')
def vm_booted_with_installer(remastered_iso, create_vms):
    host_vm, = create_vms # one single VM
    vif = host_vm.vifs()[0]
    mac_address = vif.param_get('MAC')
    logging.info("Host VM has MAC %s", mac_address)

    iso = remastered_iso
    # unique filename on server, has to work on FreeBSD-based NAS
    # too, and even v14 has no tool allowing mktemp suffixes
    remote_iso = ssh(ISOSR_SRV,
                     ["python3", "-c",
                      '"import os, tempfile; '
                      f"f = tempfile.mkstemp(suffix='.iso', dir='{ISOSR_PATH}')[1];"
                      "os.chmod(f, 0o644);"
                      'print(f);"'
                      ])
    logging.info("Uploading to ISO-SR %s as %s", iso, os.path.basename(remote_iso))
    try:
        scp(ISOSR_SRV, iso, remote_iso)
        # FIXME: run sr-scan
        host_vm.insert_cd(os.path.basename(remote_iso))

        try:
            host_vm.start()
            wait_for(host_vm.is_running, "Wait for host VM running")

            # catch host-vm IP address
            wait_for(lambda: pxe.arp_addresses_for(mac_address),
                     "Wait for DHCP server to see Host VM in ARP tables",
                     timeout_secs=10*60)
            ips = pxe.arp_addresses_for(mac_address)
            logging.info("Host VM has IPs %s", ips)
            assert len(ips) == 1
            host_vm.ip = ips[0]

            yield host_vm

            logging.info("Shutting down Host VM after successful installation")
            installer.poweroff(host_vm.ip)
            wait_for(host_vm.is_halted, "Wait for host VM halted")

        except Exception as e:
            logging.critical("caught exception %s", e)
            host_vm.shutdown(force=True)
            raise
        except KeyboardInterrupt:
            logging.warning("keyboard interrupt")
            host_vm.shutdown(force=True)
            raise

        host_vm.eject_cd()
    finally:
        logging.info("Removing %s from ISO-SR server", os.path.basename(remote_iso))
        ssh(ISOSR_SRV, ["rm", remote_iso])
