from copy import deepcopy
import importlib
import logging
import os
import pytest
import pytest_dependency
import tempfile

from lib.common import callable_marker
from lib.commands import local_cmd, scp, ssh

ISO_IMAGES = getattr(importlib.import_module('data', package=None), 'ISO_IMAGES', {})

# Return true if the version of the ISO doesn't support the source type.
# Note: this is a quick-win hack, to avoid explicit enumeration of supported
# source_type values for each ISO.
def skip_source_type(version, source_type):
    if version not in ISO_IMAGES.keys():
        return True, "version of ISO {} is unknown".format(version)

    if source_type == "iso":
        if ISO_IMAGES[version].get('net-only', False):
            return True, "ISO image is net-only while source_type is local"

        return False, "do not skip"

    if source_type == "net":
        # Net install is not valid if there is no netinstall URL
        # FIXME: ISO includes a default URL so we should be able to omit net-url
        if 'net-url' not in ISO_IMAGES[version].keys():
            return True, "net-url required for netinstall was not found for {}".format(version)

        return False, "do not skip"

    # If we don't know the source type then it is invalid
    return True, "unknown source type {}".format(source_type)

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
    # FIXME take source_type directly from its own marker
    (iso_key, source_type) = callable_marker(marker.args[0], request, param_mapping=param_mapping)

    gen_unique_uuid = marker.kwargs.get("gen_unique_uuid", False)

    skip, reason = skip_source_type(iso_key, source_type)
    if skip:
        pytest.skip(reason)

    from data import ISOSR_SRV, ISOSR_PATH, PXE_CONFIG_SERVER, TEST_SSH_PUBKEY, TOOLS
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
            import xml.etree.ElementTree as ET
            root = answerfile.getroot()
            ET.SubElement(root, "script",
                          stage="filesystem-populated",
                          type="url").text = "file:///root/postinstall.sh"
            # source is not required when doing a restore so don't try to update it
            # FIXME: should know from answerfile data that we're restoring - which
            # advocates for doing the change before conversion inside the answerfile
            # callable marker
            if source_type == "net" and not "test_restore" in request.node.nodeid:
                source_elmt = root.find("source")
                if source_elmt is None:
                    pytest.skip("source not found in answerfile")
                new_source_elmt = ET.Element('source', type='url')
                try:
                    new_source_elmt.text = ISO_IMAGES[iso_key]['net-url']
                except KeyError:
                    pytest.skip("URL not set for %s".format(iso_key))
                parent = root
                for elmt in parent:
                    if elmt.tag == 'source':
                        parent.remove(elmt)
                        parent.append(new_source_elmt)
                        break
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

cat > "$INSTALLIMG/root/test-unique-uuids.service" <<EOF
[Unit]
Description=Assign a unique host UUID at first boot
Before=xapi.service
[Service]
Type=oneshot
ExecStart=/bin/bash -ec 'DIR=/var/lib/misc; mkdir -p \\$DIR; if ! test -r \\$DIR/test-unique-uuids; then sed -i.installer -e "/^INSTALLATION_UUID=/ s/^.*\\$/INSTALLATION_UUID=\\\\'\\$(uuidgen)\\\\'/" -e "/^CONTROL_DOMAIN_UUID=/ s/^.*\\$/CONTROL_DOMAIN_UUID=\\\\'\\$(uuidgen)\\\\'/"  /etc/xensource-inventory; touch \\$DIR/test-unique-uuids; fi'
[Install]
RequiredBy=xapi.service
EOF

cat > "$INSTALLIMG/root/postinstall.sh" <<EOF
#!/bin/sh
set -ex

ROOT="\\$1"

cp /etc/systemd/system/test-pingpxe.service "\\$ROOT/etc/systemd/system/test-pingpxe.service"
cp /usr/local/sbin/test-pingpxe.sh "\\$ROOT/usr/local/sbin/test-pingpxe.sh"
systemctl --root="\\$ROOT" enable test-pingpxe.service

if [ {gen_unique_uuid} = True ]; then
    cp /root/test-unique-uuids.service "\\$ROOT/etc/systemd/system/test-unique-uuids.service"
    systemctl --root="\\$ROOT" enable test-unique-uuids.service
fi

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
