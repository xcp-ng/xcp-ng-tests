import pytest

DIRECTORIES_PATH = 'directories'

@pytest.fixture(scope='package')
def fsp_config(host):
    db_path = host.ssh(['mktemp', '-d'])
    shared_dir_path = host.ssh(['mktemp', '-d'])
    return {
        'db_path': db_path,
        'shared_dir_path': shared_dir_path
    }

@pytest.fixture(scope='package')
def fsp_sr(host, fsp_config):
    """ An FSP SR on first host. """
    db_path = fsp_config['db_path']
    host.ssh(['mkdir', db_path + '/' + DIRECTORIES_PATH])
    sr = host.sr_create('fsp', "fsp-local-SR-test", {'file-uri': db_path})
    yield sr
    # teardown
    sr.destroy()
    host.ssh(['rm', '-rf', fsp_config['db_path']])
    host.ssh(['rm', '-rf', fsp_config['shared_dir_path']])
