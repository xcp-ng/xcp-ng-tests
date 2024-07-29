from copy import deepcopy
import logging
import os
import pytest
import tempfile
import xml.etree.ElementTree as ET

from lib.installer import AnswerFile
from lib.common import callable_marker
from lib.commands import local_cmd, scp, ssh

@pytest.fixture(scope='function')
def answerfile(request):
    marker = request.node.get_closest_marker("answerfile")

    if marker is None:
        yield None              # no answerfile to generate
        return

    # construct answerfile definition from option "base", and explicit bits
    param_mapping = marker.kwargs.get("param_mapping", {})
    answerfile_def = callable_marker(marker.args[0], request, param_mapping=param_mapping)
    assert isinstance(answerfile_def, AnswerFile)

    from data import HOSTS_IP_CONFIG
    answerfile_def.top_append(
        dict(TAG="admin-interface",
             name="eth0",
             proto="static",
             CONTENTS=(
                 dict(TAG='ipaddr', CONTENTS=HOSTS_IP_CONFIG['HOSTS']['DEFAULT']),
                 dict(TAG='subnet', CONTENTS=HOSTS_IP_CONFIG['NETMASK']),
                 dict(TAG='gateway', CONTENTS=HOSTS_IP_CONFIG['GATEWAY']),
             )),
        dict(TAG="name-server",
             CONTENTS=HOSTS_IP_CONFIG['DNS']),
    )

    yield answerfile_def

@pytest.fixture(scope='function')
def iso_remaster(request, answerfile):
    marker = request.node.get_closest_marker("installer_iso")
    assert marker is not None, "iso_remaster fixture requires 'installer_iso' marker"
    param_mapping = marker.kwargs.get("param_mapping", {})
    iso_key = callable_marker(marker.args[0], request, param_mapping=param_mapping)

    from data import ISO_IMAGES, ISOSR_SRV, ISOSR_PATH, PXE_CONFIG_SERVER, TEST_SSH_PUBKEY, TOOLS
    assert "iso-remaster" in TOOLS
    iso_remaster = TOOLS["iso-remaster"]
    assert os.access(iso_remaster, os.X_OK)

    assert iso_key in ISO_IMAGES, f"ISO_IMAGES does not have a value for {iso_key}"
    SOURCE_ISO = ISO_IMAGES[iso_key]['path']

    with tempfile.TemporaryDirectory() as isotmp:
        remastered_iso = os.path.join(isotmp, "image.iso")
        img_patcher_script = os.path.join(isotmp, "img-patcher")
        iso_patcher_script = os.path.join(isotmp, "iso-patcher")
        answerfile_xml = os.path.join(isotmp, "answerfile.xml")

        if answerfile:
            logging.info("generating answerfile %s", answerfile_xml)
            answerfile.top_append(dict(TAG="script", stage="filesystem-populated",
                                       type="url", CONTENTS="file:///root/postinstall.sh"))
            answerfile.write_xml(answerfile_xml)
        else:
            logging.info("no answerfile")

        logging.info("Remastering %s to %s", SOURCE_ISO, remastered_iso)

        # generate install.img-patcher script
        with open(img_patcher_script, "xt") as patcher_fd:
            print(f"""#!/bin/bash
set -ex
INSTALLIMG="$1"

mkdir -p "$INSTALLIMG/root/.ssh"
echo "{TEST_SSH_PUBKEY}" > "$INSTALLIMG/root/.ssh/authorized_keys"

test ! -e "{answerfile_xml}" ||
    cp "{answerfile_xml}" "$INSTALLIMG/root/answerfile.xml"

cat > "$INSTALLIMG/usr/local/sbin/test-pingpxe.sh" << 'EOF'
#! /bin/bash
set -eE
set -o pipefail

ether_of () {{
    ifconfig "$1" | grep ether | sed 's/.*ether \\([^ ]*\\).*/\\1/'
}}

# on installed system, avoid xapi-project/xen-api#5799
if ! [ -e /opt/xensource/installer ]; then
    eth_mac=$(ether_of eth0)
    br_mac=$(ether_of xenbr0)

    # wait for bridge MAC to be fixed
    test "$eth_mac" = "$br_mac"
fi

ping -c1 "$1"
EOF
chmod +x "$INSTALLIMG/usr/local/sbin/test-pingpxe.sh"

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

cat > "$INSTALLIMG/root/postinstall.sh" <<EOF
#!/bin/sh
set -ex

ROOT="\\$1"

cp /etc/systemd/system/test-pingpxe.service "\\$ROOT/etc/systemd/system/test-pingpxe.service"
cp /usr/local/sbin/test-pingpxe.sh "\\$ROOT/usr/local/sbin/test-pingpxe.sh"
systemctl --root="\\$ROOT" enable test-pingpxe.service

mkdir -p "\\$ROOT/root/.ssh"
echo "{TEST_SSH_PUBKEY}" >> "\\$ROOT/root/.ssh/authorized_keys"
EOF
""",
                  file=patcher_fd)
            os.chmod(patcher_fd.fileno(), 0o755)

        # generate iso-patcher script
        with open(iso_patcher_script, "xt") as patcher_fd:
            passwd = "passw0rd" # FIXME use invalid hash
            print(f"""#!/bin/bash
set -ex
ISODIR="$1"
SED_COMMANDS=(-e "s@/vmlinuz@/vmlinuz sshpassword={passwd} atexit=shell network_device=all@")
test ! -e "{answerfile_xml}" ||
    SED_COMMANDS+=(-e "s@/vmlinuz@/vmlinuz install answerfile=file:///root/answerfile.xml@")

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
                   SOURCE_ISO, remastered_iso
                   ])

        # unique filename on server, has to work on FreeBSD-based NAS
        # too, and even v14 has no tool allowing mktemp suffixes
        remote_iso = ssh(ISOSR_SRV,
                         ["python3", "-c",
                          '"import os, tempfile; '
                          f"f = tempfile.mkstemp(suffix='.iso', dir='{ISOSR_PATH}')[1];"
                          "os.chmod(f, 0o644);"
                          'print(f);"'
                          ])
        logging.info("Uploading to ISO-SR server remastered %s as %s",
                     remastered_iso, os.path.basename(remote_iso))
        scp(ISOSR_SRV, remastered_iso, remote_iso)
        # FIXME: is sr-scan ever needed?

    try:
        yield os.path.basename(remote_iso)
    finally:
        logging.info("Removing %s from ISO-SR server", os.path.basename(remote_iso))
        ssh(ISOSR_SRV, ["rm", remote_iso])
