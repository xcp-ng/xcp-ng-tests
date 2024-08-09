import logging
import os
import pytest

from lib.common import callable_marker
from lib.commands import scp, ssh

from data import ISO_IMAGES, ISOSR_SRV, ISOSR_PATH

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
def vm_booted_with_installer(installer_iso, create_vms):
    host_vm, = create_vms # one single VM
    iso = installer_iso

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
        yield host_vm
        host_vm.eject_cd()
    finally:
        logging.info("Removing %s from ISO-SR server", os.path.basename(remote_iso))
        ssh(ISOSR_SRV, ["rm", remote_iso])
