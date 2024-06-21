from copy import deepcopy
import logging
import os
import pytest
import pytest_dependency
import tempfile

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
    marker_def = deepcopy(callable_marker(marker.args[0], request, param_mapping=param_mapping))
    if "base" in marker_def:
        from data import BASE_ANSWERFILES
        answerfile_def = deepcopy(BASE_ANSWERFILES[marker_def["base"]])
        del marker_def["base"]
    else:
        answerfile_def = {}
    answerfile_def.update(marker_def)

    # convert to a ElementTree.Element tree suitable for further
    # modification before we serialize it to XML
    import xml.etree.ElementTree as ET

    # root element special case
    root_def = answerfile_def.pop("root")
    root_tag = root_def.pop("tag")
    root = ET.Element(root_tag, **root_def)

    # contents of root element
    for name, defn in answerfile_def.items():
        text = defn.pop("text", None)
        element = ET.SubElement(root, name, **defn)
        if text:
            element.text = text

    yield ET.ElementTree(root)

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
    SOURCE_ISO = ISO_IMAGES[iso_key]

    with tempfile.TemporaryDirectory() as isotmp:
        remastered_iso = os.path.join(isotmp, "image.iso")
        img_patcher_script = os.path.join(isotmp, "img-patcher")
        iso_patcher_script = os.path.join(isotmp, "iso-patcher")
        answerfile_xml = os.path.join(isotmp, "answerfile.xml")

        if answerfile:
            logging.info("generating answerfile %s", answerfile_xml)
            import xml.etree.ElementTree as ET
            ET.SubElement(answerfile.getroot(), "script",
                          stage="filesystem-populated",
                          type="url").text = "file:///root/postinstall.sh"
            answerfile.write(answerfile_xml)
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

cat > "$INSTALLIMG/etc/systemd/system/test-pingpxe.service" <<EOF
[Unit]
Description=Ping pxe server to populate its ARP table
After=network-online.target
[Service]
Type=oneshot
ExecStart=/bin/sh -c 'while ! ping -c1 {PXE_CONFIG_SERVER}; do sleep 1 ; done'
[Install]
WantedBy=default.target
EOF

systemctl --root="$INSTALLIMG" enable test-pingpxe.service

cat > "$INSTALLIMG/root/postinstall.sh" <<EOF
#!/bin/sh
set -ex

ROOT="\\$1"

cp /etc/systemd/system/test-pingpxe.service "\\$ROOT/etc/systemd/system/test-pingpxe.service"
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

@pytest.fixture(scope='function')
def xcpng_chained(request):
    # take test name from mark
    marker = request.node.get_closest_marker("continuation_of")
    assert marker is not None, "xcpng_chained fixture requires 'continuation_of' marker"
    param_mapping = marker.kwargs.get("param_mapping", {})
    continuation_of = callable_marker(marker.args[0], request, param_mapping=param_mapping)

    vm_defs = [dict(name=vm_spec['vm'],
                    image_test=vm_spec['image_test'],
                    image_vm=vm_spec.get("image_vm", vm_spec['vm']),
                    image_scope=vm_spec.get("scope", "module"),
                    )
               for vm_spec in continuation_of]

    depends = [vm_spec['image_test'] for vm_spec in continuation_of]
    pytest_dependency.depends(request, depends)
    request.applymarker(pytest.mark.vm_definitions(*vm_defs))
