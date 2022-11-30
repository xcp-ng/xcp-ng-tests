import pytest

FSP_REPO_NAME = 'runx'

FSP_PACKAGES = ['xcp-ng-xapi-storage']

DIRECTORIES_PATH = 'directories'

def install_fsp(host):
    host.add_xcpng_repo(FSP_REPO_NAME)
    host.yum_save_state()
    host.yum_install(FSP_PACKAGES)

def uninstall_fsp(host):
    host.yum_restore_saved_state()
    host.remove_xcpng_repo(FSP_REPO_NAME)

@pytest.fixture(scope='package')
def host_with_fsp(host):
    install_fsp(host)
    yield host
    uninstall_fsp(host)

@pytest.fixture(scope='package')
def fsp_config(host_with_fsp):
    db_path = host_with_fsp.ssh(['mktemp', '-d'])
    shared_dir_path = host_with_fsp.ssh(['mktemp', '-d'])
    return {
        'db_path': db_path,
        'shared_dir_path': shared_dir_path
    }

@pytest.fixture(scope='package')
def fsp_sr(host_with_fsp, fsp_config):
    """ An FSP SR on first host. """
    db_path = fsp_config['db_path']
    host_with_fsp.ssh(['mkdir', db_path + '/' + DIRECTORIES_PATH])
    sr = host_with_fsp.sr_create('fsp', "fsp-local-SR-test", {'file-uri': db_path})
    yield sr
    # teardown
    sr.destroy()
    host_with_fsp.ssh(['rm', '-rf', fsp_config['db_path']])
    host_with_fsp.ssh(['rm', '-rf', fsp_config['shared_dir_path']])
