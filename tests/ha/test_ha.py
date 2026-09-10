# Requirements:
# - --hosts: host(A1) + hostA2 in a 2-node pool
# - data.py: NFS_DEVICE_CONFIG (shared NFS for VM disks + HA heartbeat)
# - --vm: small agile linux VM
# - tests/ha/data.py: Moonshot CARTRIDGES map (power-off recovery)
#   cp tests/ha/data.py-dist tests/ha/data.py
from __future__ import annotations

import pytest

import logging
import time

from lib.common import wait_for
from lib.host import Host
from lib.sr import SR
from lib.vm import VM
from tests.ha.ha import (
    HA_FAILOVER_TIMEOUT_SECS,
    HA_FENCE_FAILOVER_TIMEOUT_SECS,
    HA_HOST_RECOVERY_TIMEOUT_SECS,
    HA_PARTITION_SETTLE_SECS,
    cleanup_after_fence,
    disable_pool_ha,
    enable_ha_with_vm,
    master_and_other,
    nfs_server,
    pool_master,
    power_off,
    recover_host,
    wait_for_vm_on_host,
)
from tests.ha.iptables import (
    NFS_BLOCK_PORTS,
    install_persistent_rules,
    iptables_rules,
    output_drop_specs,
)
from tests.ha.net import host_is_reachable

def _disable_ha_best_effort(pool) -> None:
    try:
        disable_pool_ha(pool)
    except Exception:
        logging.warning('Could not disable pool HA after scenario', exc_info=True)


@pytest.mark.complex_prerequisites
@pytest.mark.small_vm
@pytest.mark.usefixtures('hostA2')
class TestHaScenario:
    @pytest.mark.reboot
    def test_master_failure(self, host: Host, ha_protected_vm: VM, nfs_sr: SR) -> None:
        pool = host.pool
        failed, survivor = master_and_other(pool)
        try:
            enable_ha_with_vm(ha_protected_vm, failed, pool, nfs_sr)
            power_off(failed)
            failed.wait_for_host_down()
            pool_master(pool)
            wait_for_vm_on_host(
                ha_protected_vm,
                survivor,
                f'Wait for VM to restart on {survivor}',
                timeout_secs=HA_FENCE_FAILOVER_TIMEOUT_SECS,
            )
        finally:
            _disable_ha_best_effort(pool)
            try:
                recover_host(failed)
            except Exception:
                logging.warning('Could not recover %s after host failure test', failed, exc_info=True)

    @pytest.mark.reboot
    def test_slave_failure(self, host: Host, ha_protected_vm: VM, nfs_sr: SR) -> None:
        pool = host.pool
        master, failed = master_and_other(pool)
        survivor = master
        try:
            enable_ha_with_vm(ha_protected_vm, failed, pool, nfs_sr)
            power_off(failed)
            failed.wait_for_host_down()
            pool_master(pool)
            wait_for_vm_on_host(
                ha_protected_vm,
                survivor,
                f'Wait for VM to restart on {survivor}',
                timeout_secs=HA_FENCE_FAILOVER_TIMEOUT_SECS,
            )
        finally:
            _disable_ha_best_effort(pool)
            try:
                recover_host(failed)
            except Exception:
                logging.warning('Could not recover %s after host failure test', failed, exc_info=True)

    @pytest.mark.reboot
    def test_master_failure_with_recovery(self, host: Host, ha_protected_vm: VM, nfs_sr: SR) -> None:
        pool = host.pool
        failed, survivor = master_and_other(pool)
        recovered = False
        try:
            enable_ha_with_vm(ha_protected_vm, failed, pool, nfs_sr)
            power_off(failed)
            failed.wait_for_host_down()
            pool_master(pool)
            wait_for_vm_on_host(
                ha_protected_vm,
                survivor,
                f'Wait for VM to restart on {survivor}',
                timeout_secs=HA_FENCE_FAILOVER_TIMEOUT_SECS,
            )
            _disable_ha_best_effort(pool)
            recover_host(failed)
            assert host_is_reachable(failed)
            assert failed.is_enabled()
            recovered = True
        finally:
            if not recovered:
                _disable_ha_best_effort(pool)
                try:
                    recover_host(failed)
                except Exception:
                    logging.warning('Could not recover %s after host failure test', failed, exc_info=True)

    @pytest.mark.reboot
    def test_slave_failure_with_recovery(self, host: Host, ha_protected_vm: VM, nfs_sr: SR) -> None:
        pool = host.pool
        master, failed = master_and_other(pool)
        survivor = master
        recovered = False
        try:
            enable_ha_with_vm(ha_protected_vm, failed, pool, nfs_sr)
            power_off(failed)
            failed.wait_for_host_down()
            pool_master(pool)
            wait_for_vm_on_host(
                ha_protected_vm,
                survivor,
                f'Wait for VM to restart on {survivor}',
                timeout_secs=HA_FENCE_FAILOVER_TIMEOUT_SECS,
            )
            _disable_ha_best_effort(pool)
            recover_host(failed)
            assert host_is_reachable(failed)
            assert failed.is_enabled()
            recovered = True
        finally:
            if not recovered:
                _disable_ha_best_effort(pool)
                try:
                    recover_host(failed)
                except Exception:
                    logging.warning('Could not recover %s after host failure test', failed, exc_info=True)

    @pytest.mark.reboot
    def test_xhad_crash_on_slave(self, host: Host, ha_protected_vm: VM, nfs_sr: SR) -> None:
        pool = host.pool
        survivor, failed = master_and_other(pool)
        try:
            enable_ha_with_vm(ha_protected_vm, failed, pool, nfs_sr)
            try:
                logging.info('Kill xhad on %s', failed)
                failed.ssh('kill -9 "$(pidof -s xhad)"')
                failed.wait_for_host_down(timeout_secs=HA_FAILOVER_TIMEOUT_SECS)
                assert host_is_reachable(survivor)
                pool_master(pool, host=survivor, timeout_secs=HA_FENCE_FAILOVER_TIMEOUT_SECS)
                wait_for_vm_on_host(
                    ha_protected_vm,
                    survivor,
                    f'Wait for VM to restart on {survivor} after xhad fencing',
                    timeout_secs=HA_FENCE_FAILOVER_TIMEOUT_SECS,
                )
            finally:
                try:
                    logging.info('Wait for %s to come back after fence reboot', failed)
                    failed.wait_for_host_up(timeout_secs=HA_HOST_RECOVERY_TIMEOUT_SECS)
                    failed.wait_for_ssh_reachable(timeout_secs=HA_HOST_RECOVERY_TIMEOUT_SECS)
                    failed.wait_for_xapi_enabled(timeout_secs=HA_HOST_RECOVERY_TIMEOUT_SECS)
                except Exception:
                    logging.warning('Could not wait for %s after xhad crash test', failed, exc_info=True)
        finally:
            _disable_ha_best_effort(pool)

    @pytest.mark.reboot
    def test_xhad_crash_on_master(self, host: Host, ha_protected_vm: VM, nfs_sr: SR) -> None:
        pool = host.pool
        failed, survivor = master_and_other(pool)
        try:
            enable_ha_with_vm(ha_protected_vm, failed, pool, nfs_sr)
            try:
                logging.info('Kill xhad on %s', failed)
                failed.ssh('kill -9 "$(pidof -s xhad)"')
                failed.wait_for_host_down(timeout_secs=HA_FAILOVER_TIMEOUT_SECS)
                assert host_is_reachable(survivor)
                pool_master(pool, host=survivor, timeout_secs=HA_FENCE_FAILOVER_TIMEOUT_SECS)
                wait_for_vm_on_host(
                    ha_protected_vm,
                    survivor,
                    f'Wait for VM to restart on {survivor} after xhad fencing',
                    timeout_secs=HA_FENCE_FAILOVER_TIMEOUT_SECS,
                )
            finally:
                try:
                    logging.info('Wait for %s to come back after fence reboot', failed)
                    failed.wait_for_host_up(timeout_secs=HA_HOST_RECOVERY_TIMEOUT_SECS)
                    failed.wait_for_ssh_reachable(timeout_secs=HA_HOST_RECOVERY_TIMEOUT_SECS)
                    failed.wait_for_xapi_enabled(timeout_secs=HA_HOST_RECOVERY_TIMEOUT_SECS)
                except Exception:
                    logging.warning('Could not wait for %s after xhad crash test', failed, exc_info=True)
        finally:
            _disable_ha_best_effort(pool)

    def test_xapi_crash_on_slave(self, host: Host, ha_protected_vm: VM, nfs_sr: SR) -> None:
        pool = host.pool
        master, target = master_and_other(pool)
        try:
            enable_ha_with_vm(ha_protected_vm, target, pool, nfs_sr)
            logging.info('Kill xapi on %s', target)
            target.ssh('kill -9 "$(pidof -s xapi)"')
            wait_for(
                lambda: host_is_reachable(target) and target.is_enabled(),
                f'Wait for xapi to come back on {target}',
                timeout_secs=HA_FAILOVER_TIMEOUT_SECS,
                retry_delay_secs=5,
            )
            target.wait_for_xapi_enabled(timeout_secs=HA_FAILOVER_TIMEOUT_SECS)
            assert host_is_reachable(master) and master.is_enabled()
            assert master.is_master()
            wait_for_vm_on_host(
                ha_protected_vm,
                target,
                f'Wait for VM to remain on {target} after xapi restart',
            )
        finally:
            _disable_ha_best_effort(pool)

    def test_xapi_crash_on_master(self, host: Host, ha_protected_vm: VM, nfs_sr: SR) -> None:
        pool = host.pool
        target, other = master_and_other(pool)
        try:
            enable_ha_with_vm(ha_protected_vm, target, pool, nfs_sr)
            logging.info('Kill xapi on %s', target)
            target.ssh('kill -9 "$(pidof -s xapi)"')
            wait_for(
                lambda: host_is_reachable(target) and target.is_enabled(),
                f'Wait for xapi to come back on {target}',
                timeout_secs=HA_FAILOVER_TIMEOUT_SECS,
                retry_delay_secs=5,
            )
            target.wait_for_xapi_enabled(timeout_secs=HA_FAILOVER_TIMEOUT_SECS)
            assert host_is_reachable(other) and other.is_enabled()
            assert target.is_master()
            wait_for_vm_on_host(
                ha_protected_vm,
                target,
                f'Wait for VM to remain on {target} after xapi restart',
            )
        finally:
            _disable_ha_best_effort(pool)

    def test_nfs_unreachable_pool(self, host: Host, ha_protected_vm: VM, nfs_sr: SR) -> None:
        pool = host.pool
        vm_host, other = master_and_other(pool)
        server = nfs_server(nfs_sr)
        try:
            enable_ha_with_vm(ha_protected_vm, vm_host, pool, nfs_sr)
            host_rules = [
                (h, spec)
                for h in (vm_host, other)
                for spec in output_drop_specs(server, ports=NFS_BLOCK_PORTS)
            ]
            with iptables_rules(host_rules):
                logging.info(
                    'Assert %s stay reachable for %ss (no fence)',
                    ', '.join(str(h) for h in (vm_host, other)),
                    HA_PARTITION_SETTLE_SECS,
                )
                deadline = time.monotonic() + HA_PARTITION_SETTLE_SECS
                while True:
                    for h in (vm_host, other):
                        assert host_is_reachable(h), f'{h} became unreachable during settle window'
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(min(10, remaining))
                wait_for_vm_on_host(
                    ha_protected_vm,
                    vm_host,
                    f'Wait for VM to remain on {vm_host} while NFS is unreachable pool-wide',
                )
        finally:
            _disable_ha_best_effort(pool)

    @pytest.mark.reboot
    def test_nfs_unreachable_on_master(self, host: Host, ha_protected_vm: VM, nfs_sr: SR) -> None:
        pool = host.pool
        blocked, survivor = master_and_other(pool)
        rule_specs = output_drop_specs(nfs_server(nfs_sr), ports=NFS_BLOCK_PORTS)
        try:
            enable_ha_with_vm(ha_protected_vm, blocked, pool, nfs_sr)
            try:
                install_persistent_rules(blocked, rule_specs)
                assert host_is_reachable(survivor)
                pool_master(pool, host=survivor, timeout_secs=HA_FENCE_FAILOVER_TIMEOUT_SECS)
                wait_for_vm_on_host(
                    ha_protected_vm,
                    survivor,
                    f'Wait for VM to restart on {survivor} after NFS loss on {blocked}',
                    timeout_secs=HA_FENCE_FAILOVER_TIMEOUT_SECS,
                )
            finally:
                cleanup_after_fence(blocked, rule_specs)
        finally:
            _disable_ha_best_effort(pool)

    @pytest.mark.reboot
    def test_nfs_unreachable_on_slave(self, host: Host, ha_protected_vm: VM, nfs_sr: SR) -> None:
        pool = host.pool
        survivor, blocked = master_and_other(pool)
        rule_specs = output_drop_specs(nfs_server(nfs_sr), ports=NFS_BLOCK_PORTS)
        try:
            enable_ha_with_vm(ha_protected_vm, blocked, pool, nfs_sr)
            try:
                install_persistent_rules(blocked, rule_specs)
                assert host_is_reachable(survivor)
                pool_master(pool, host=survivor, timeout_secs=HA_FENCE_FAILOVER_TIMEOUT_SECS)
                wait_for_vm_on_host(
                    ha_protected_vm,
                    survivor,
                    f'Wait for VM to restart on {survivor} after NFS loss on {blocked}',
                    timeout_secs=HA_FENCE_FAILOVER_TIMEOUT_SECS,
                )
            finally:
                cleanup_after_fence(blocked, rule_specs)
        finally:
            _disable_ha_best_effort(pool)
