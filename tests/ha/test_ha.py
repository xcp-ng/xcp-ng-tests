# Requirements:
# - --hosts: host(A1) + hostA2 in a 2-node pool
# - data.py: NFS_DEVICE_CONFIG (shared NFS for VM disks + HA heartbeat)
# - --vm: small agile linux VM
# - data.py: HOSTS[..].power_control (type=moonshot, chassis/slot/user/password) for power-off recovery
#
# Each test reads the master/slave roles from the pool: a scenario leaves the survivor as master.
from __future__ import annotations

import pytest

import logging
import time
from functools import partial

from lib.common import Defer
from lib.host import Host
from lib.sr import SR
from lib.vm import VM
from tests.ha.ha import (
    HA_FAILOVER_TIMEOUT_SECS,
    HA_SETTLE_SECS,
    assert_not_rebooted,
    cleanup_ha_scenario,
    enable_pool_ha,
    host_boot_id,
    prepare_pool_for_ha,
    recover_from_fence,
    start_ha_vm,
    wait_for_failover,
    wait_for_fence_reboot,
    wait_for_vm_on_host,
)
from tests.ha.iptables import (
    clear_persistent_rules,
    delete_rule,
    insert_rule,
    install_persistent_rules,
    nfs_drop_specs,
    peer_network_drop_specs,
)

ROLES = ['master', 'slave']


@pytest.mark.complex_prerequisites
@pytest.mark.small_vm
class TestHaScenario:
    @pytest.mark.reboot
    @pytest.mark.parametrize('failed_role', ROLES)
    def test_host_failure(
        self, failed_role: str, host: Host, hostA2: Host, ha_protected_vm: VM, nfs_sr: SR, defer: Defer
    ) -> None:
        pool = host.pool
        master, slave = prepare_pool_for_ha(pool)
        failed, survivor = (master, slave) if failed_role == 'master' else (slave, master)
        defer(lambda: cleanup_ha_scenario(pool))

        start_ha_vm(ha_protected_vm, on_host=failed)
        enable_pool_ha(pool, nfs_sr)
        failed.hard_power_off(verify=True)
        wait_for_failover(pool, ha_protected_vm, survivor, f'after hard power-off of {failed}')
        recover_from_fence(pool, failed, survivor)

    @pytest.mark.reboot
    @pytest.mark.parametrize('failed_role', ROLES)
    def test_xhad_crash(
        self, failed_role: str, host: Host, hostA2: Host, ha_protected_vm: VM, nfs_sr: SR, defer: Defer
    ) -> None:
        pool = host.pool
        master, slave = prepare_pool_for_ha(pool)
        failed, survivor = (master, slave) if failed_role == 'master' else (slave, master)
        defer(lambda: cleanup_ha_scenario(pool))

        start_ha_vm(ha_protected_vm, on_host=failed)
        enable_pool_ha(pool, nfs_sr)
        boot_id = host_boot_id(failed)
        logging.info(f'Kill xhad on {failed}')
        failed.ssh('kill -9 "$(pidof -s xhad)"')
        failed.wait_for_host_down(timeout_secs=HA_FAILOVER_TIMEOUT_SECS)
        wait_for_failover(pool, ha_protected_vm, survivor, f'after xhad fencing of {failed}')
        wait_for_fence_reboot(failed, boot_id)
        recover_from_fence(pool, failed, survivor)

    @pytest.mark.parametrize('target_role', ROLES)
    def test_xapi_crash(
        self, target_role: str, host: Host, hostA2: Host, ha_protected_vm: VM, nfs_sr: SR, defer: Defer
    ) -> None:
        pool = host.pool
        master, slave = prepare_pool_for_ha(pool)
        target = master if target_role == 'master' else slave
        defer(lambda: cleanup_ha_scenario(pool))

        start_ha_vm(ha_protected_vm, on_host=target)
        enable_pool_ha(pool, nfs_sr)
        boot_ids = {h: host_boot_id(h) for h in pool.hosts}
        old_pid = target.xapi_pid()
        logging.info(f'Kill xapi on {target}')
        target.ssh(f'kill -9 {old_pid}')
        target.wait_for_xapi_restart(old_pid, timeout_secs=HA_FAILOVER_TIMEOUT_SECS)
        target.wait_for_xapi_enabled(timeout_secs=HA_FAILOVER_TIMEOUT_SECS)
        assert_not_rebooted(boot_ids)
        assert master.is_master(), f'{master} should remain pool master'
        assert slave.is_enabled() and not slave.is_master(), f'{slave} should remain an enabled slave'
        wait_for_vm_on_host(ha_protected_vm, target, f'Wait for VM to remain on {target} after xapi restart')

    def test_nfs_unreachable_pool(
        self, host: Host, hostA2: Host, ha_protected_vm: VM, nfs_sr: SR, defer: Defer
    ) -> None:
        pool = host.pool
        master, slave = prepare_pool_for_ha(pool)
        server = nfs_sr.nfs_server()
        defer(lambda: cleanup_ha_scenario(pool))

        start_ha_vm(ha_protected_vm, on_host=master)
        enable_pool_ha(pool, nfs_sr)
        boot_ids = {h: host_boot_id(h) for h in pool.hosts}
        for h in (master, slave):
            for spec in nfs_drop_specs(server):
                insert_rule(h, spec)
                defer(partial(delete_rule, h, spec))

        logging.info(f'Let HA run {HA_SETTLE_SECS}s without NFS on any host')
        time.sleep(HA_SETTLE_SECS)

        assert_not_rebooted(boot_ids)
        assert pool.is_ha_enabled(), 'HA should stay enabled while NFS is unreachable pool-wide'
        assert master.is_master(), f'{master} should remain pool master'
        assert not slave.is_master(), f'{slave} should remain slave'
        assert master.is_enabled() and slave.is_enabled(), 'Neither host should be disabled'
        # The VM disk is on the blocked NFS: don't wait for the guest OS.
        wait_for_vm_on_host(
            ha_protected_vm,
            master,
            f'Wait for VM to remain on {master} while NFS is unreachable pool-wide',
            booted=False,
        )

    @pytest.mark.reboot
    @pytest.mark.parametrize('failed_role', ROLES)
    @pytest.mark.parametrize('cut', ['nfs', 'network+nfs'])
    def test_nfs_unreachable(
        self, cut: str, failed_role: str, host: Host, hostA2: Host, ha_protected_vm: VM, nfs_sr: SR, defer: Defer
    ) -> None:
        """Cut NFS on one host, alone or with the peer network: it loses the statefile and fences.

        With the peer network cut too, it fences whatever its UUID.
        """
        pool = host.pool
        master, slave = prepare_pool_for_ha(pool)
        failed, survivor = (master, slave) if failed_role == 'master' else (slave, master)
        rule_specs = nfs_drop_specs(nfs_sr.nfs_server())
        if cut == 'network+nfs':
            rule_specs += peer_network_drop_specs(survivor.hostname_or_ip)
        defer(lambda: cleanup_ha_scenario(pool))

        start_ha_vm(ha_protected_vm, on_host=failed)
        enable_pool_ha(pool, nfs_sr)
        boot_id = host_boot_id(failed)
        install_persistent_rules(failed, rule_specs)
        wait_for_failover(pool, ha_protected_vm, survivor, f'after {cut} loss on {failed}')
        wait_for_fence_reboot(failed, boot_id)
        recover_from_fence(pool, failed, survivor, after_ssh=clear_persistent_rules)

    @pytest.mark.reboot
    def test_network_unreachable(
        self, host: Host, hostA2: Host, ha_protected_vm: VM, nfs_sr: SR, defer: Defer
    ) -> None:
        """In an equal network partition, HA keeps the host with the lowest UUID.

        The UUIDs decide which host fences, so the master/slave roles are not parametrized:
        the VM starts on the host that will fence.
        """
        pool = host.pool
        master, slave = prepare_pool_for_ha(pool)
        survivor = min(master, slave, key=lambda h: h.uuid)
        fenced = slave if survivor is master else master
        logging.info(f'Network partition: {survivor} should survive, {fenced} should fence')
        defer(lambda: cleanup_ha_scenario(pool))

        start_ha_vm(ha_protected_vm, on_host=fenced)
        enable_pool_ha(pool, nfs_sr)
        boot_id = host_boot_id(fenced)
        install_persistent_rules(fenced, peer_network_drop_specs(survivor.hostname_or_ip))
        fenced.wait_for_host_down(timeout_secs=HA_FAILOVER_TIMEOUT_SECS)
        wait_for_failover(pool, ha_protected_vm, survivor, 'after network partition (UUID survivor)')
        wait_for_fence_reboot(fenced, boot_id)
        recover_from_fence(pool, fenced, survivor, after_ssh=clear_persistent_rules)
