from __future__ import annotations

import logging

from lib.common import wait_for
from lib.host import Host
from lib.pool import Pool
from lib.vm import VM

HA_FAILOVER_TIMEOUT_SECS = 5 * 60

def pool_master(pool: Pool) -> Host:
    master: Host | None = None
    for pool_host in pool.hosts:
        if pool_host.is_master():
            assert master is None, "There are more than one master host"
            master = pool_host
    assert master is not None, "No master found from pool of hosts: %s" % ", ".join(
        h.hostname_or_ip for h in pool.hosts
    )
    return master

def assert_pool_healthy(pool: Pool) -> Host:
    master = pool_master(pool)
    assert pool.param_get("ha-enabled") == "true", "pool HA must stay enabled"
    return master

def master_failure_test(host: Host, hostA2: Host, vm: VM) -> None:
    pool = host.pool
    failed_master = assert_pool_healthy(pool)
    other = failed_master.pool.first_host_that_isnt(failed_master)
    assert(other != None)
    vm.start(on=failed_master.uuid)
    vm.wait_for_os_booted()

    try:
        failed_master.reboot()
        failed_master.wait_for_host_down()
        wait_for(other.is_master, "Wait for member host to be promoted to master",
                 timeout_secs=HA_FAILOVER_TIMEOUT_SECS)
        assert_pool_healthy(pool)
        assert vm.is_running()
        assert vm.is_running_on_host(other)
    finally:
        try:
            failed_master.wait_for_host_up()
            failed_master.wait_for_ssh_reachable()
            failed_master.wait_for_xapi_enabled()
        except Exception:
            logging.warning("Could not power on %s during cleanup", failed_master)

        if vm.is_running():
            vm.shutdown(verify=True)


def slave_failure_test(host: Host, hostA2: Host, vm: VM) -> None:
    pool = host.pool
    master = assert_pool_healthy(pool)
    other = master.pool.first_host_that_isnt(master)
    assert(other != None)
    vm.start(on=other.uuid)
    vm.wait_for_os_booted()

    try:
        other.reboot()
        wait_for(lambda: vm.is_running_on_host(master), "Wait for VM to restart on master",
                 timeout_secs=HA_FAILOVER_TIMEOUT_SECS)
        assert_pool_healthy(pool)
        assert vm.is_running()
        assert vm.is_running_on_host(master)
    finally:
        try:
            other.wait_for_host_up()
            other.wait_for_ssh_reachable()
            other.wait_for_xapi_enabled()
        except Exception:
            logging.warning("Could not power on %s during cleanup", other)

        if vm.is_running():
            vm.shutdown(verify=True)


def host_rejoin_test(host: Host, hostA2: Host, vm: VM) -> None:
    pool = host.pool
    master = assert_pool_healthy(pool)
    other = master.pool.first_host_that_isnt(master)
    assert(other != None)
    vm.start(on=other.uuid)
    vm.wait_for_os_booted()

    try:
        other.reboot(verify=True)
        assert_pool_healthy(pool)
        wait_for(other.is_enabled, "Wait for rejoined host to become enabled",
                 timeout_secs=HA_FAILOVER_TIMEOUT_SECS)
        assert other.is_enabled()
    finally:
        try:
            other.wait_for_host_up()
            other.wait_for_ssh_reachable()
            other.wait_for_xapi_enabled()
        except Exception:
            logging.warning("Could not power on %s during cleanup", other)

        if vm.is_running():
            vm.shutdown(verify=True)
