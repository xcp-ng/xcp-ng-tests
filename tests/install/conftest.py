import logging
import os
import pytest

from lib.common import callable_marker, url_download

from data import ISO_IMAGES, ISO_IMAGES_BASE, ISO_IMAGES_CACHE

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

@pytest.fixture(scope='function')
def vm_booted_with_installer(host, create_vms, installer_iso):
    host_vm, = create_vms # one single VM
    iso = installer_iso['iso']

    remote_iso = None
    try:
        remote_iso = host.pool.push_iso(iso)
        host_vm.insert_cd(os.path.basename(remote_iso))
        yield host_vm
        host_vm.eject_cd()
    finally:
        if remote_iso:
            host.pool.remove_iso(remote_iso)
