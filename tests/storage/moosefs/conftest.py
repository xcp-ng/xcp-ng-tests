from __future__ import annotations

import pytest

import logging

from lib import config

# explicit import for package-scope fixtures
from lib.host import Host
from lib.pool import Pool
from lib.sr import SR
from lib.vdi import VDI
from lib.vm import VM
from pkgfixtures import pool_with_saved_yum_state

from typing import Generator

def install_moosefs(host: Host) -> None:
    assert not host.file_exists('/usr/sbin/mount.moosefs'), \
        "MooseFS client should not be installed on the host before all tests"
    host.ssh('curl https://repository.moosefs.com/RPM-GPG-KEY-MooseFS > /etc/pki/rpm-gpg/RPM-GPG-KEY-MooseFS')
    host.ssh('curl http://repository.moosefs.com/MooseFS-3-el7.repo > /etc/yum.repos.d/MooseFS.repo')
    host.yum_install(['fuse', 'moosefs-client'])

def uninstall_moosefs_repo(host: Host) -> None:
    host.ssh('rm -f /etc/pki/rpm-gpg/RPM-GPG-KEY-MooseFS /etc/yum.repos.d/MooseFS.repo')

def enable_moosefs(host: Host) -> None:
    host.activate_smapi_driver('moosefs')

def disable_moosefs(host: Host) -> None:
    host.deactivate_smapi_driver('moosefs')

@pytest.fixture(scope='package')
def pool_with_moosefs_installed(pool_with_saved_yum_state: Pool) -> Generator[Pool, None, None]:
    pool = pool_with_saved_yum_state
    pool.exec_on_hosts_on_error_rollback(install_moosefs, uninstall_moosefs_repo)
    yield pool
    pool.exec_on_hosts_on_error_continue(uninstall_moosefs_repo)

@pytest.fixture(scope='package')
def pool_with_moosefs_enabled(pool_with_moosefs_installed: Pool) -> Generator[Pool, None, None]:
    pool = pool_with_moosefs_installed
    pool.exec_on_hosts_on_error_rollback(enable_moosefs, disable_moosefs)
    yield pool
    pool.exec_on_hosts_on_error_continue(disable_moosefs)

@pytest.fixture(scope='package')
def moosefs_device_config() -> dict[str, str]:
    return config.sr_device_config("MOOSEFS_DEVICE_CONFIG")

@pytest.fixture(scope='package')
def moosefs_sr(moosefs_device_config: dict[str, str], pool_with_moosefs_enabled: Pool) -> Generator[SR, None, None]:
    """ MooseFS SR on a specific host. """
    sr = pool_with_moosefs_enabled.master.sr_create('moosefs', "MooseFS-SR-test", moosefs_device_config, shared=True)
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_moosefs_sr(moosefs_sr: SR) -> Generator[VDI, None, None]:
    vdi = moosefs_sr.create_vdi('MooseFS-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_moosefs_sr(host: Host, moosefs_sr: SR, vm_ref: str) -> Generator[VM, None, None]:
    vm = host.import_vm(vm_ref, sr_uuid=moosefs_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
