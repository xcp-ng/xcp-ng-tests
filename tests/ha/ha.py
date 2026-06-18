from __future__ import annotations

import logging
import time

import pytest

import lib.commands as commands
from lib.common import safe_split, wait_for
from lib.host import Host
from lib.pool import Pool
from lib.sr import SR
from lib.vm import VM
from tests.ha.iptables import (
    NFS_BLOCK_PORTS,
    delete_all,
    iptables_rules,
    output_drop_specs,
    peer_cut_specs,
)
from tests.ha.moonshot import power_on_host
from tests.ha.net import host_is_reachable

HA_FAILOVER_TIMEOUT_SECS = 5 * 60
HA_HOST_RECOVERY_TIMEOUT_SECS = 15 * 60
HA_POOL_HA_ENABLED_TIMEOUT_SECS = 5 * 60
HA_VM_ARMED_TIMEOUT_SECS = 2 * 60
HA_PARTITION_SETTLE_SECS = 2 * 60


def _transient_xapi_error(exc: commands.SSHCommandFailed) -> bool:
    msg = exc.stdout.lower()
    return (
        'still live' in msg
        or 'connection refused' in msg
        or 'missing table' in msg
        or 'invalid object reference' in msg
    )


def _host_considered_dead(master: Host, failed_host: Host) -> bool:
    try:
        if failed_host.uuid not in safe_split(master.xe('host-list', minimal=True)):
            return True
        return (
            master.xe(
                'host-param-get',
                {'uuid': failed_host.uuid, 'param-name': 'host-metrics-live'},
            )
            == 'false'
        )
    except commands.SSHCommandFailed as exc:
        if _transient_xapi_error(exc):
            return False
        raise


def _pool_master(pool: Pool, *, timeout_secs: int = HA_FAILOVER_TIMEOUT_SECS) -> Host:
    def find_master() -> bool:
        for host in pool.hosts:
            if host_is_reachable(host) and host.is_master() and host.is_enabled():
                pool.master = host
                return True
        return False

    wait_for(
        find_master,
        'Wait for a reachable pool master',
        timeout_secs=timeout_secs,
        retry_delay_secs=5,
    )
    return pool.master


def _disable_pool_ha(pool: Pool, master: Host) -> None:
    def disable_if_enabled() -> bool:
        try:
            if pool.param_get('ha-enabled') != 'true':
                return True
            logging.info('Disable pool HA')
            master.xe('pool-ha-disable')
            return True
        except commands.SSHCommandFailed as exc:
            if _transient_xapi_error(exc):
                logging.info(
                    'Transient XAPI error while disabling pool HA, retrying: %s',
                    exc.stdout.strip(),
                )
                return False
            raise

    wait_for(
        disable_if_enabled,
        'Disable pool HA',
        timeout_secs=HA_FAILOVER_TIMEOUT_SECS,
        retry_delay_secs=5,
    )


def _prepare_ha_teardown(pool: Pool) -> Host:
    master = _pool_master(pool, timeout_secs=HA_FAILOVER_TIMEOUT_SECS)
    _disable_pool_ha(pool, master)
    _forget_unreachable_hosts(pool, master)
    return master


def _enable_pool_ha(pool: Pool, heartbeat_sr_uuid: str) -> None:
    master = _pool_master(pool)
    if pool.param_get('ha-enabled') == 'true':
        logging.info('Disable pool HA to refresh failover plan')
        master.xe('pool-ha-disable')
    logging.info('Enable pool HA (heartbeat SR %s)', heartbeat_sr_uuid)
    max_fail = master.xe('pool-ha-compute-max-host-failures-to-tolerate').strip()
    if max_fail == '0':
        pytest.skip('Pool cannot tolerate any host failure with current capacity')
    pool.param_set('ha-host-failures-to-tolerate', max_fail)
    master.xe('pool-ha-enable', {'heartbeat-sr-uuids': heartbeat_sr_uuid})
    wait_for(
        lambda: pool.param_get('ha-enabled') == 'true',
        'Wait for pool HA to become enabled',
        timeout_secs=HA_POOL_HA_ENABLED_TIMEOUT_SECS,
    )


def _nfs_server(nfs_sr: SR) -> str:
    pbd_uuids = nfs_sr.pbd_uuids()
    assert pbd_uuids, f'NFS SR {nfs_sr.uuid} has no PBDs'
    server = nfs_sr.pool.master.xe(
        'pbd-param-get',
        {'uuid': pbd_uuids[0], 'param-name': 'device-config', 'param-key': 'server'},
    ).strip()
    assert server, f'NFS SR {nfs_sr.uuid} PBD has no device-config server'
    return server


def _prepare_ha_protected_running_vm(vm: VM, on_host: Host, pool: Pool, nfs_sr: SR) -> None:
    for member in pool.hosts:
        assert host_is_reachable(member), f'{member} must be reachable before HA scenario'
        assert member.is_enabled(), f'{member} must be enabled before HA scenario'

    vm.host = _pool_master(pool)
    vm.param_set('ha-restart-priority', 'restart')
    if vm.is_running():
        vm.shutdown(force=True)
    vm.start(on=on_host.uuid)
    vm.wait_for_os_booted()
    _enable_pool_ha(pool, nfs_sr.uuid)
    wait_for(
        lambda: vm.param_get('ha-always-run') == 'true',
        'Wait for VM to be armed for HA',
        timeout_secs=HA_VM_ARMED_TIMEOUT_SECS,
    )


def _unplanned_power_off(host: Host) -> None:
    logging.info('Power off %s', host)
    try:
        # SysRq o: immediate poweroff. SSH may fail once shutdown starts.
        host.ssh('sync; echo 1 > /proc/sys/kernel/sysrq; echo o > /proc/sysrq-trigger')
    except commands.SSHCommandFailed:
        pass
    try:
        host.wait_for_host_down(timeout_secs=30)
    except TimeoutError:
        logging.warning('SysRq poweroff did not take %s down, falling back to scheduled poweroff', host)
        host.ssh('systemd-run --on-active=2s poweroff')


def _unplanned_host_failure(pool: Pool, failed_host: Host) -> None:
    _unplanned_power_off(failed_host)
    failed_host.wait_for_host_down()
    master = _pool_master(pool)
    master.wait_for_xapi_enabled(timeout_secs=HA_FAILOVER_TIMEOUT_SECS)
    wait_for(
        lambda: _host_considered_dead(master, failed_host),
        f'Wait for {failed_host} to be considered dead',
        timeout_secs=HA_FAILOVER_TIMEOUT_SECS,
        retry_delay_secs=10,
    )


def _wait_for_vm_on_host(vm: VM, on_host: Host, pool: Pool, msg: str) -> None:
    vm.host = _pool_master(pool)

    def check() -> bool:
        try:
            return vm.is_running_on_host(on_host)
        except commands.SSHCommandFailed as exc:
            logging.warning('VM state check failed: %s', exc)
            return False

    wait_for(check, msg, timeout_secs=HA_FAILOVER_TIMEOUT_SECS)


def _assert_host_alive(host: Host) -> None:
    assert host_is_reachable(host), f'{host} should still be reachable'
    assert host.is_enabled(), f'{host} should still be enabled'


def _assert_fence_failover(
    pool: Pool,
    ha_protected_vm: VM,
    *,
    failed_host: Host,
    survivor: Host,
    vm_msg: str,
) -> None:
    assert host_is_reachable(survivor), f'{survivor} should still be reachable'
    current_master = _pool_master(pool)
    assert current_master.uuid == survivor.uuid, (
        f'Expected {survivor} to be pool master after fencing on {failed_host}, '
        f'got {current_master}'
    )
    _wait_for_vm_on_host(ha_protected_vm, survivor, pool, vm_msg)


def _wait_for_host_back(host: Host, *, timeout_secs: int = HA_HOST_RECOVERY_TIMEOUT_SECS) -> None:
    logging.info('Wait for %s to come back after fence reboot', host)
    host.wait_for_host_up(timeout_secs=timeout_secs)
    host.wait_for_ssh_reachable(timeout_secs=timeout_secs)
    host.wait_for_xapi_enabled(timeout_secs=timeout_secs)


def _try_wait_for_host_back(host: Host, *, context: str) -> None:
    try:
        _wait_for_host_back(host)
    except Exception:
        logging.warning('Could not wait for %s %s', host, context, exc_info=True)


def _cleanup_after_fence(host: Host, rule_specs: list[str]) -> None:
    try:
        _wait_for_host_back(host)
    finally:
        logging.info('Remove leftover iptables rules on %s', host)
        delete_all(host, rule_specs)


def _recover_host(host: Host, *, timeout_secs: int = HA_HOST_RECOVERY_TIMEOUT_SECS) -> None:
    logging.info('Recover %s via Moonshot power-on', host)
    power_on_host(str(host.hostname_or_ip))
    host.wait_for_host_up(timeout_secs=timeout_secs)
    host.wait_for_ssh_reachable(timeout_secs=timeout_secs)
    host.wait_for_xapi_enabled(timeout_secs=timeout_secs)


def _try_recover_host(host: Host, *, context: str) -> None:
    try:
        _recover_host(host)
    except Exception:
        logging.warning('Could not recover %s %s', host, context, exc_info=True)


def _kill_named_process(host: Host, name: str) -> None:
    logging.info('Kill %s on %s', name, host)
    host.ssh(f'kill -9 "$(pidof -s {name})"')


def _assert_hosts_stay_reachable(
    hosts: tuple[Host, ...],
    *,
    duration_secs: int,
    poll_secs: int = 10,
) -> None:
    logging.info(
        'Assert %s stay reachable for %ss (no fence)',
        ', '.join(str(h) for h in hosts),
        duration_secs,
    )
    deadline = time.monotonic() + duration_secs
    while True:
        for host in hosts:
            assert host_is_reachable(host), (
                f'{host} became unreachable during {duration_secs}s settle window'
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_secs, remaining))


def run_host_failure_test(
    pool: Pool,
    ha_protected_vm: VM,
    nfs_sr: SR,
    *,
    failed_host: Host,
    survivor: Host,
    assert_recovery: bool = False,
) -> None:
    recovered = False
    try:
        _prepare_ha_protected_running_vm(ha_protected_vm, failed_host, pool, nfs_sr)
        _unplanned_host_failure(pool, failed_host)
        _wait_for_vm_on_host(
            ha_protected_vm,
            survivor,
            pool,
            f'Wait for VM to restart on {survivor}',
        )
        if assert_recovery:
            _recover_host(failed_host)
            _assert_host_alive(failed_host)
            recovered = True
    finally:
        if not recovered:
            _try_recover_host(
                failed_host,
                context=(
                    'after host failure recovery test'
                    if assert_recovery
                    else 'after host failure test'
                ),
            )


def run_xhad_crash_test(
    pool: Pool,
    ha_protected_vm: VM,
    nfs_sr: SR,
    *,
    failed_host: Host,
    survivor: Host,
) -> None:
    _prepare_ha_protected_running_vm(ha_protected_vm, failed_host, pool, nfs_sr)
    try:
        _kill_named_process(failed_host, 'xhad')
        failed_host.wait_for_host_down(timeout_secs=HA_FAILOVER_TIMEOUT_SECS)
        _assert_fence_failover(
            pool,
            ha_protected_vm,
            failed_host=failed_host,
            survivor=survivor,
            vm_msg=f'Wait for VM to restart on {survivor} after xhad fencing',
        )
    finally:
        _try_wait_for_host_back(failed_host, context='after xhad crash test')

    _assert_host_alive(survivor)


def run_xapi_crash_test(
    pool: Pool,
    ha_protected_vm: VM,
    nfs_sr: SR,
    *,
    target_host: Host,
    other_host: Host,
) -> None:
    _prepare_ha_protected_running_vm(ha_protected_vm, target_host, pool, nfs_sr)
    target_was_master = target_host.is_master()

    _kill_named_process(target_host, 'xapi')

    def xapi_back() -> bool:
        return host_is_reachable(target_host) and target_host.is_enabled()

    wait_for(
        xapi_back,
        f'Wait for xapi to come back on {target_host}',
        timeout_secs=HA_FAILOVER_TIMEOUT_SECS,
        retry_delay_secs=5,
    )
    target_host.wait_for_xapi_enabled(timeout_secs=HA_FAILOVER_TIMEOUT_SECS)

    _assert_host_alive(other_host)
    if target_was_master:
        assert target_host.is_master(), f'{target_host} should remain pool master after xapi restart'
    else:
        assert other_host.is_master(), (
            f'{other_host} should remain pool master after xapi restart on {target_host}'
        )
    _wait_for_vm_on_host(
        ha_protected_vm,
        target_host,
        pool,
        f'Wait for VM to remain on {target_host} after xapi restart',
    )


def run_nfs_unreachable_pool_test(
    pool: Pool,
    ha_protected_vm: VM,
    nfs_sr: SR,
    *,
    vm_host: Host,
    other_host: Host,
) -> None:
    server = _nfs_server(nfs_sr)
    _prepare_ha_protected_running_vm(ha_protected_vm, vm_host, pool, nfs_sr)

    host_rules = [
        (host, spec)
        for host in (vm_host, other_host)
        for spec in output_drop_specs(server, ports=NFS_BLOCK_PORTS)
    ]
    with iptables_rules(host_rules):
        _assert_hosts_stay_reachable(
            (vm_host, other_host),
            duration_secs=HA_PARTITION_SETTLE_SECS,
        )
        _assert_host_alive(vm_host)
        _assert_host_alive(other_host)
        _wait_for_vm_on_host(
            ha_protected_vm,
            vm_host,
            pool,
            f'Wait for VM to remain on {vm_host} while NFS is unreachable pool-wide',
        )


def run_nfs_unreachable_one_host_test(
    pool: Pool,
    ha_protected_vm: VM,
    nfs_sr: SR,
    *,
    blocked_host: Host,
    survivor: Host,
) -> None:
    server = _nfs_server(nfs_sr)
    _prepare_ha_protected_running_vm(ha_protected_vm, blocked_host, pool, nfs_sr)

    rule_specs = output_drop_specs(server, ports=NFS_BLOCK_PORTS)
    try:
        with iptables_rules([(blocked_host, spec) for spec in rule_specs]):
            blocked_host.wait_for_host_down(timeout_secs=HA_FAILOVER_TIMEOUT_SECS)
            _assert_fence_failover(
                pool,
                ha_protected_vm,
                failed_host=blocked_host,
                survivor=survivor,
                vm_msg=f'Wait for VM to restart on {survivor} after NFS loss on {blocked_host}',
            )
    finally:
        _cleanup_after_fence(blocked_host, rule_specs)

    _assert_host_alive(survivor)


def run_management_network_cut_test(
    pool: Pool,
    ha_protected_vm: VM,
    nfs_sr: SR,
    *,
    cut_host: Host,
    survivor: Host,
) -> None:
    server = _nfs_server(nfs_sr)
    if server == str(survivor.hostname_or_ip) or server == str(cut_host.hostname_or_ip):
        pytest.skip(
            f'NFS server {server} is a pool member, cannot cut management without also '
            'cutting storage'
        )

    _prepare_ha_protected_running_vm(ha_protected_vm, cut_host, pool, nfs_sr)

    rule_specs = peer_cut_specs(survivor)
    try:
        with iptables_rules([(cut_host, spec) for spec in rule_specs]):
            cut_host.wait_for_host_down(timeout_secs=HA_FAILOVER_TIMEOUT_SECS)
            _assert_fence_failover(
                pool,
                ha_protected_vm,
                failed_host=cut_host,
                survivor=survivor,
                vm_msg=f'Wait for VM to restart on {survivor} after management cut on {cut_host}',
            )
    finally:
        _cleanup_after_fence(cut_host, rule_specs)

    _assert_host_alive(survivor)


def _forget_host(master: Host, host_uuid: str) -> None:
    if host_uuid == '<not in database>':
        return
    logging.info('Forget pool host %s', host_uuid)
    try:
        master.xe('host-forget', {'uuid': host_uuid}, force=True)
    except commands.SSHCommandFailed:
        try:
            master.xe('host-declare-dead', {'uuid': host_uuid}, force=True)
        except commands.SSHCommandFailed:
            pass
        try:
            master.xe('host-forget', {'uuid': host_uuid}, force=True)
        except commands.SSHCommandFailed as exc:
            logging.warning('Could not forget host %s: %s', host_uuid, exc.stdout.strip())


def _forget_unreachable_hosts(pool: Pool, master: Host) -> None:
    for host_uuid in pool.hosts_uuids():
        if host_uuid == master.uuid:
            continue
        pool_host = pool.get_host_by_uuid(host_uuid)
        if host_is_reachable(pool_host):
            continue
        _forget_host(master, host_uuid)


def _destroy_managed_vdis(sr: SR, master: Host) -> None:
    try:
        sr.scan()
    except commands.SSHCommandFailed as exc:
        if 'no attached pbd' not in exc.stdout.lower() and 'not attached' not in exc.stdout.lower():
            raise
    for vdi_uuid in sr.vdi_uuids(managed=True):
        logging.info('Destroy managed VDI %s on SR %s', vdi_uuid, sr.uuid)
        try:
            master.xe('vdi-destroy', {'uuid': vdi_uuid})
        except commands.SSHCommandFailed as exc:
            logging.warning('Could not destroy managed VDI %s: %s', vdi_uuid, exc.stdout.strip())


def destroy_nfs_sr_after_ha(pool: Pool, sr: SR) -> None:
    _prepare_ha_teardown(pool)
    _destroy_managed_vdis(sr, pool.master)
    try:
        sr.destroy(verify=True, force=True)
    except Exception:
        logging.warning('SR destroy failed, forgetting %s', sr.uuid, exc_info=True)
        sr.forget(force=True)


def destroy_ha_protected_vm(pool: Pool, vm: VM) -> None:
    master = _prepare_ha_teardown(pool)
    vm.host = master
    vm.param_clear('ha-restart-priority')
    if vm.exists():
        vm.destroy(verify=True)
