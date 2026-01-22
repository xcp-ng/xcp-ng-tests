import pytest

import logging

# Explicitly import package-scoped fixtures (see explanation in pkgfixtures.py)
from lib.host import Host
from pkgfixtures import host_with_saved_yum_state

@pytest.fixture(scope="package")
def host_without_mlx_compat_loaded(host_with_saved_yum_state: Host):
    host = host_with_saved_yum_state

    # We need to check if mlx_compat module is loaded. If it is already loaded,
    # we need to unload it before starting the test and load it again when the test ends.
    mlx_compat_loaded = host.ssh_with_result('lsmod | grep mlx_compat').returncode == 0
    if mlx_compat_loaded:
        logging.info("mlx_compat is loaded so unload it before testing")
        host.ssh('modprobe -r -v mlx_compat')
    else:
        logging.info("mlx_compat is not loaded")

    yield host

    # Reload the mlx_compat module if it was loaded when starting the test
    if mlx_compat_loaded:
        logging.info("test is done so reload mlx_compat")
        host.ssh('modprobe -v mlx_compat')

@pytest.fixture(scope="package")
def host_without_mlx_card(host: Host):
    if host.ssh_with_result('lspci | grep Mellanox').returncode == 0:
        # Skip test to not mess with mellanox card
        pytest.skip("This test can't be run on a host with a mellanox card")
    yield host
