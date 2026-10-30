import pytest

import logging

from lib.common import Defer, is_uuid, vm_image
from lib.host import Host
from lib.pool import Pool
from lib.sr import SR
from lib.vm import VM
from lib.xo import xo_cli

@pytest.fixture
def up_to_date_pool(host: Host) -> Pool:
    pool = host.pool
    assert len(pool.hosts) >= 2, "RPU tests require a pool with at least 2 hosts"
    assert not any(h.has_updates() for h in pool.hosts), "RPU tests require the hosts to be up to date"
    return pool


def get_vm_url_or_uuid(vm_ref: str) -> str:
    if is_uuid(vm_ref) or vm_ref.startswith("http"):
        return vm_ref
    return vm_image(vm_ref)


def import_and_start_vm(pool: Pool, shared_sr: SR, vm_url_or_uuid: str, defer: Defer) -> VM:
    from data import CACHE_IMPORTED_VM

    host = pool.master
    if is_uuid(vm_url_or_uuid):
        vm_orig = VM(vm_url_or_uuid, host)
        name = vm_orig.name()
        logging.info(">> Reuse VM %s (%s) on host %s" % (vm_url_or_uuid, name, host))
    else:
        assert vm_url_or_uuid.startswith("http")
        vm_orig = host.import_vm(vm_url_or_uuid, shared_sr.uuid, use_cache=CACHE_IMPORTED_VM)

    if CACHE_IMPORTED_VM:
        logging.info(">> Clone cached VM before running tests")
        vm = vm_orig.clone()
    else:
        vm = vm_orig

    def cleanup() -> None:
        if CACHE_IMPORTED_VM or not is_uuid(vm_url_or_uuid):
            logging.info("<< Destroy VM")
            vm.destroy(verify=True)

    defer(cleanup)

    if not vm.is_running():
        vm.start()

    return vm


@pytest.fixture
def pool_ready_for_rpu(
    up_to_date_pool: Pool, shared_sr: SR, pytestconfig: pytest.Config, defer: Defer
) -> tuple[Pool, list[VM]]:
    pool = up_to_date_pool
    vm_defs = pytestconfig.getoption("vm")
    assert vm_defs, "RPU tests require at least one VM in --vm"
    vm_url_or_uuids = [get_vm_url_or_uuid(vm_def) for vm_def in vm_defs]

    # Downgrade to an old xo-lite (this only affects the xo-lite package)
    for host in pool.hosts:
        host.ssh('yum downgrade -y xo-lite --disablerepo="*" --enablerepo=xcp-ng-base')
        defer(lambda host=host: host.ssh("yum update -y xo-lite"))

    vms = [import_and_start_vm(pool, shared_sr, vm_url_or_uuid, defer) for vm_url_or_uuid in vm_url_or_uuids]

    for vm in vms:
        vm.wait_for_vm_running_and_ssh_up()
        logging.info(f"{vm.name()} runs on {vm.get_residence_host().hostname_or_ip}")

    return pool, vms


@pytest.mark.reboot
def test_rpu_without_xo(pool_ready_for_rpu: tuple[Pool, list[VM]], defer: Defer) -> None:
    pool, vms = pool_ready_for_rpu

    # Make sure master is first in the list
    hosts = [pool.master, *(host for host in pool.hosts if host != pool.master)]

    # RPU on all hosts
    for host in hosts:
        logging.info(f"Updating host {host.hostname_or_ip}")
        host.xe("host-disable", args={"uuid": host.uuid})
        defer(lambda host=host: host.xe("host-enable", args={"uuid": host.uuid}))
        host.xe("host-evacuate", args={"uuid": host.uuid})
        host.ssh("yum update -y xo-lite")
        host.reboot(verify=True)
        host.xe("host-enable", args={"uuid": host.uuid})
        logging.info(f"Host {host.hostname_or_ip} updated")
        for vm in vms:
            assert vm.is_ssh_up()
    assert not any(h.has_updates() for h in pool.hosts), "The hosts are not up to date after the RPU"


@pytest.mark.reboot
def test_rpu_with_xo(pool_ready_for_rpu: tuple[Pool, list[VM]], hosts_with_xo: list[Host]) -> None:
    pool, vms = pool_ready_for_rpu
    logging.info("Starting rolling update")
    result = xo_cli("pool.rollingUpdate", {"pool": pool.uuid})
    assert result.strip() == "true"
    assert not any(h.has_updates() for h in pool.hosts), "The hosts are not up to date after the RPU"
    logging.info("Rolling update completed")
    for vm in vms:
        assert vm.is_ssh_up()
