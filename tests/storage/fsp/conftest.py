import pytest

import time

# Explicitly import package-scoped fixtures (see explanation in pkgfixtures.py)
from lib.host import Host
from lib.sr import SR
from pkgfixtures import host_with_saved_yum_state_toolstack_restart

from typing import Generator

FSP_REPO_NAME = 'runx'

FSP_PACKAGES = ['xcp-ng-xapi-storage']

DIRECTORIES_PATH = 'directories'

@pytest.fixture(scope='package')
def host_with_runx_repo(host_with_saved_yum_state_toolstack_restart: Host) -> Generator[Host, None, None]:
    host = host_with_saved_yum_state_toolstack_restart
    host.add_xcpng_repo(FSP_REPO_NAME, 'vates')
    yield host
    # teardown
    host.remove_xcpng_repo(FSP_REPO_NAME)

@pytest.fixture(scope='package')
def host_with_fsp(host_with_runx_repo: Host) -> Generator[Host, None, None]:
    host = host_with_runx_repo
    host.yum_install(FSP_PACKAGES)
    # fsp is not listed by xe sm-list until it's actually used, so just wait a few seconds instead
    # wait_for(lambda: host.xe('sm-list', {'type': 'fsp'}).strip() != '', "Wait for fsp to be available")
    time.sleep(3)
    yield host
    # teardown: nothing to do, done by host_with_saved_yum_state.

@pytest.fixture(scope='package')
def fsp_config(host_with_fsp: Host) -> dict[str, str]:
    db_path = host_with_fsp.ssh('mktemp -d')
    shared_dir_path = host_with_fsp.ssh('mktemp -d')
    return {
        'db_path': db_path,
        'shared_dir_path': shared_dir_path
    }

@pytest.fixture(scope='package')
def fsp_sr(host_with_fsp: Host, fsp_config: dict[str, str]) -> Generator[SR, None, None]:
    """ An FSP SR on first host. """
    db_path = fsp_config['db_path']
    host_with_fsp.ssh(f'mkdir {db_path}/{DIRECTORIES_PATH}')
    sr = host_with_fsp.sr_create('fsp', "fsp-local-SR-test", {'file-uri': db_path})
    yield sr
    # teardown
    sr.destroy()
    host_with_fsp.ssh(f'rm -rf {fsp_config["db_path"]}')
    host_with_fsp.ssh(f'rm -rf {fsp_config["shared_dir_path"]}')
