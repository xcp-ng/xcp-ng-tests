import logging
import os
import pytest
import tempfile

from lib import installer, pxe
from lib.common import callable_marker, url_download, wait_for
from lib.commands import local_cmd

from data import (ANSWERFILE_URL, ISO_IMAGES, ISO_IMAGES_BASE, ISO_IMAGES_CACHE,
                  PXE_CONFIG_SERVER, TEST_SSH_PUBKEY, TOOLS)

@pytest.fixture(scope='function')
def installer_iso(request):
    iso_key = request.node.get_closest_marker("iso_version").args[0]
    assert iso_key in ISO_IMAGES, f"ISO_IMAGES does not have a value for {iso_key}"
    iso = ISO_IMAGES[iso_key]['path']
    if iso.startswith("/"):
        assert os.path.exists(iso), f"file not found: {iso}"
        local_iso = iso
    else:
        cached_iso = os.path.join(ISO_IMAGES_CACHE, os.path.basename(iso))
        if not os.path.exists(cached_iso):
            url = iso if ":/" in iso else (ISO_IMAGES_BASE + iso)
            logging.info("installer_iso: downloading %r into %r", url, cached_iso)
            url_download(url, cached_iso)
        local_iso = cached_iso
    logging.info("installer_iso: using %r", local_iso)
    return dict(iso=local_iso,
                )

# Remasters the ISO sepecified by `installer_iso` mark, with:
# - network and ssh support activated, and .ssh/authorized_key so tests can
#   go probe installation process
# - a test-pingpxe.service running in installer system, to make it possible
#   for the test to determine the dynamic IP obtained during installation
# - atexit=shell to prevent the system from spontaneously rebooting
# - an answerfile to make the install process non-interactive (downloaded from URL)
# - a postinstall script to modify the installed system with:
#   - the same .ssh/authorized_key
@pytest.fixture(scope='function')
def remastered_iso(installer_iso):
    iso_file = installer_iso['iso']
    assert "iso-remaster" in TOOLS
    iso_remaster = TOOLS["iso-remaster"]
    assert os.access(iso_remaster, os.X_OK)

    with tempfile.TemporaryDirectory() as isotmp:
        remastered_iso = os.path.join(isotmp, "image.iso")
        img_patcher_script = os.path.join(isotmp, "img-patcher")
        iso_patcher_script = os.path.join(isotmp, "iso-patcher")

        logging.info("Remastering %s to %s", iso_file, remastered_iso)

        # generate install.img-patcher script
        with open(img_patcher_script, "xt") as patcher_fd:
            script_contents = f"""#!/bin/bash
set -ex
INSTALLIMG="$1"

mkdir -p "$INSTALLIMG/root/.ssh"
echo "{TEST_SSH_PUBKEY}" > "$INSTALLIMG/root/.ssh/authorized_keys"

mkdir -p "$INSTALLIMG/usr/local/sbin"
cat > "$INSTALLIMG/usr/local/sbin/test-pingpxe.sh" << 'EOF'
#! /bin/bash
set -eE
set -o pipefail

if [ $(readlink "/bin/ping") = busybox ]; then
    # XS before 7.0
    PINGARGS=""
else
    PINGARGS="-c1"
fi

ping $PINGARGS "$1"
EOF
chmod +x "$INSTALLIMG/usr/local/sbin/test-pingpxe.sh"

if [ -d "$INSTALLIMG/etc/systemd/system" ]; then
    cat > "$INSTALLIMG/etc/systemd/system/test-pingpxe.service" <<EOF
[Unit]
Description=Ping pxe server to populate its ARP table
After=network-online.target
[Service]
Type=oneshot
ExecStart=/bin/sh -c 'while ! /usr/local/sbin/test-pingpxe.sh "{PXE_CONFIG_SERVER}"; do sleep 1 ; done'
[Install]
WantedBy=default.target
EOF

    systemctl --root="$INSTALLIMG" enable test-pingpxe.service
else # sysv scripts for before XS 7.x
    cat > "$INSTALLIMG/etc/init.d/S12test-pingpxe" <<'EOF'
#!/bin/sh
case "$1" in
  start)
    sh -c 'while ! /usr/local/sbin/test-pingpxe.sh "{PXE_CONFIG_SERVER}"; do sleep 1 ; done' & ;;
  stop) ;;
esac
EOF

    chmod +x "$INSTALLIMG/etc/init.d/S12test-pingpxe"
fi

cat > "$INSTALLIMG/root/postinstall.sh" <<'EOF'
#!/bin/sh
set -ex

ROOT="$1"

mkdir -p "$ROOT/root/.ssh"
echo "{TEST_SSH_PUBKEY}" >> "$ROOT/root/.ssh/authorized_keys"
EOF
"""
            print(script_contents, file=patcher_fd)
            os.chmod(patcher_fd.fileno(), 0o755)

        # generate iso-patcher script
        with open(iso_patcher_script, "xt") as patcher_fd:
            passwd = "passw0rd" # FIXME use invalid hash?
            script_contents = f"""#!/bin/bash
set -ex
ISODIR="$1"
SED_COMMANDS=(-e "s@/vmlinuz@/vmlinuz network_device=all sshpassword={passwd} atexit=shell@")
SED_COMMANDS+=(-e "s@/vmlinuz@/vmlinuz install answerfile={ANSWERFILE_URL}@")


shopt -s nullglob # there may be no grub config, eg for XS 6.5 and earlier
sed -i "${{SED_COMMANDS[@]}}" \
    "$ISODIR"/*/*/grub*.cfg \
    "$ISODIR"/boot/isolinux/isolinux.cfg
"""
            print(script_contents, file=patcher_fd)
            os.chmod(patcher_fd.fileno(), 0o755)

        # do remaster
        local_cmd([iso_remaster,
                   "--install-patcher", img_patcher_script,
                   "--iso-patcher", iso_patcher_script,
                   iso_file, remastered_iso
                   ])

        yield remastered_iso

@pytest.fixture(scope='function')
def vm_booted_with_installer(host, create_vms, remastered_iso):
    host_vm, = create_vms # one single VM
    iso = remastered_iso

    vif = host_vm.vifs()[0]
    mac_address = vif.param_get('MAC')
    logging.info("Host VM has MAC %s", mac_address)

    remote_iso = None
    try:
        remote_iso = host.pool.push_iso(iso)
        host_vm.insert_cd(os.path.basename(remote_iso))

        try:
            host_vm.start()
            wait_for(host_vm.is_running, "Wait for host VM running")

            # catch host-vm IP address
            wait_for(lambda: pxe.arp_addresses_for(mac_address),
                     "Wait for DHCP server to see Host VM in ARP tables",
                     timeout_secs=10 * 60)
            ips = pxe.arp_addresses_for(mac_address)
            logging.info("Host VM has IPs %s", ips)
            assert len(ips) == 1
            host_vm.ip = ips[0]

            yield host_vm

            logging.info("Shutting down Host VM")
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
        if remote_iso:
            host.pool.remove_iso(remote_iso)
