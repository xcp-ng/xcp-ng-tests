from __future__ import annotations

import pytest

import logging

import lib.commands as commands
from lib.common import wait_for
from lib.host import Host
from lib.pool import Pool
from lib.sr import SR
from lib.vm import VM
from tests.ha.iptables import remove_persistent_rules
from tests.ha.moonshot import power_on_host
from tests.ha.net import host_is_reachable

HA_FAILOVER_TIMEOUT_SECS = 5 * 60
HA_FENCE_FAILOVER_TIMEOUT_SECS = 10 * 60
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
        or 'cannot connect to redo log' in msg
    )


def pool_master(
    pool: Pool,
    *,
    host: Host | None = None,
    timeout_secs: int = HA_FAILOVER_TIMEOUT_SECS,
) -> Host:
    def find_master() -> bool:
        candidates = (host,) if host is not None else tuple(pool.hosts)
        for candidate in candidates:
            try:
                if host_is_reachable(candidate) and candidate.is_master() and candidate.is_enabled():
                    pool.master = candidate
                    return True
            except commands.SSHCommandFailed as exc:
                if _transient_xapi_error(exc):
                    return False
                raise
        return False

    msg = f'Wait for {host} to be pool master' if host is not None else 'Wait for a reachable pool master'
    wait_for(find_master, msg, timeout_secs=timeout_secs, retry_delay_secs=5)
    return pool.master


def master_and_other(pool: Pool) -> tuple[Host, Host]:
    master = pool_master(pool)
    others = [h for h in pool.hosts if h.uuid != master.uuid]
    assert len(others) == 1, f'Expected a 2-node pool, got {len(pool.hosts)} hosts'
    logging.info('Current pool master %s, other %s', master, others[0])
    return master, others[0]


def _clear_stale_ha_static_vdis(pool: Pool, master: Host) -> None:
    for host in pool.hosts:
        if not host_is_reachable(host):
            continue
        listing = host.ssh('static-vdis list', check=False) or ''
        if not isinstance(listing, str):
            continue
        for line in listing.splitlines():
            if 'vdi-uuid' not in line.lower() or ':' not in line:
                continue
            vdi_uuid = line.split(':', 1)[1].strip()
            if not vdi_uuid:
                continue
            try:
                if master.xe('vdi-list', {'uuid': vdi_uuid}, minimal=True) == vdi_uuid:
                    continue
            except commands.SSHCommandFailed:
                pass
            logging.warning('Remove stale HA static-vdi %s on %s', vdi_uuid, host)
            host.ssh(f'static-vdis del {vdi_uuid}', check=False)


def disable_pool_ha(pool: Pool) -> None:
    master = pool_master(pool)
    cleared_stale = False

    def disable_if_enabled() -> bool:
        nonlocal cleared_stale
        try:
            if pool.param_get('ha-enabled') != 'true':
                return True
            master.xe('pool-ha-disable')
            return pool.param_get('ha-enabled') != 'true'
        except commands.SSHCommandFailed as exc:
            if _transient_xapi_error(exc):
                return False
            msg = exc.stdout.lower()
            if not cleared_stale and 'uuid you supplied was invalid' in msg and 'vdi' in msg:
                logging.warning('pool-ha-disable hit missing HA VDI; clearing stale static-vdis')
                _clear_stale_ha_static_vdis(pool, master)
                cleared_stale = True
                return False
            raise

    wait_for(disable_if_enabled, 'Disable pool HA', timeout_secs=HA_FAILOVER_TIMEOUT_SECS, retry_delay_secs=5)


def enable_ha_with_vm(vm: VM, on_host: Host, pool: Pool, nfs_sr: SR) -> None:
    for member in pool.hosts:
        assert host_is_reachable(member), f'{member} must be reachable before HA scenario'
        assert member.is_enabled(), f'{member} must be enabled before HA scenario'

    vm.host = pool_master(pool)
    vm.param_set('ha-restart-priority', 'restart')
    if vm.is_running():
        vm.shutdown(force=True)
    vm.start(on=on_host.uuid)
    vm.wait_for_os_booted()

    master = pool_master(pool)
    logging.info('Enable pool HA (heartbeat SR %s)', nfs_sr.uuid)
    max_fail = master.xe('pool-ha-compute-max-host-failures-to-tolerate').strip()
    if max_fail == '0':
        pytest.skip('Pool cannot tolerate any host failure with current capacity')
    pool.param_set('ha-host-failures-to-tolerate', max_fail)
    master.xe('pool-ha-enable', {'heartbeat-sr-uuids': nfs_sr.uuid})
    wait_for(
        lambda: pool.param_get('ha-enabled') == 'true',
        'Wait for pool HA to become enabled',
        timeout_secs=HA_POOL_HA_ENABLED_TIMEOUT_SECS,
    )
    wait_for(
        lambda: vm.param_get('ha-always-run') == 'true',
        'Wait for VM to be armed for HA',
        timeout_secs=HA_VM_ARMED_TIMEOUT_SECS,
    )


def nfs_server(nfs_sr: SR) -> str:
    pbd_uuids = nfs_sr.pbd_uuids()
    assert pbd_uuids, f'NFS SR {nfs_sr.uuid} has no PBDs'
    server = nfs_sr.pool.master.xe(
        'pbd-param-get',
        {'uuid': pbd_uuids[0], 'param-name': 'device-config', 'param-key': 'server'},
    ).strip()
    assert server, f'NFS SR {nfs_sr.uuid} PBD has no device-config server'
    return server


def power_off(host: Host) -> None:
    logging.info('Power off %s', host)
    try:
        host.ssh('sync; echo 1 > /proc/sys/kernel/sysrq; echo o > /proc/sysrq-trigger')
    except commands.SSHCommandFailed:
        pass
    try:
        host.wait_for_host_down(timeout_secs=30)
    except TimeoutError:
        logging.warning('SysRq poweroff did not take %s down, falling back to scheduled poweroff', host)
        host.ssh('systemd-run --on-active=2s poweroff')


def wait_for_vm_on_host(
    vm: VM,
    on_host: Host,
    msg: str,
    *,
    timeout_secs: int = HA_FAILOVER_TIMEOUT_SECS,
) -> None:
    vm.host = on_host

    def check() -> bool:
        try:
            return vm.is_running_on_host(on_host)
        except commands.SSHCommandFailed as exc:
            logging.warning('VM state check failed: %s', exc)
            return False

    wait_for(check, msg, timeout_secs=timeout_secs)


def recover_host(host: Host) -> None:
    logging.info('Recover %s via Moonshot power-on', host)
    power_on_host(str(host.hostname_or_ip))
    host.wait_for_host_up(timeout_secs=HA_HOST_RECOVERY_TIMEOUT_SECS)
    host.wait_for_ssh_reachable(timeout_secs=HA_HOST_RECOVERY_TIMEOUT_SECS)
    host.wait_for_xapi_enabled(timeout_secs=HA_HOST_RECOVERY_TIMEOUT_SECS)


def cleanup_after_fence(host: Host, rule_specs: list[str]) -> None:
    logging.info('Wait for %s SSH after fence reboot', host)
    host.wait_for_host_up(timeout_secs=HA_HOST_RECOVERY_TIMEOUT_SECS)
    host.wait_for_ssh_reachable(timeout_secs=HA_HOST_RECOVERY_TIMEOUT_SECS)
    wait_for(
        lambda: remove_persistent_rules(host, rule_specs),
        f'Clear persistent NFS block on {host}',
        timeout_secs=HA_HOST_RECOVERY_TIMEOUT_SECS,
        retry_delay_secs=5,
    )
    host.wait_for_xapi_enabled(timeout_secs=HA_HOST_RECOVERY_TIMEOUT_SECS)


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


def _forget_busy_nfs_sr(sr: SR, pool: Pool) -> None:
    mount_path = f'/run/sr-mount/{sr.uuid}'
    master = pool.master
    for host in pool.hosts:
        if host_is_reachable(host):
            logging.info('Lazy umount %s on %s', mount_path, host)
            host.ssh(f'umount -lf {mount_path}', check=False)
    for pbd_uuid in sr.pbd_uuids():
        try:
            master.xe('pbd-unplug', {'uuid': pbd_uuid})
        except commands.SSHCommandFailed:
            pass
        try:
            logging.info('Destroy PBD %s', pbd_uuid)
            master.xe('pbd-destroy', {'uuid': pbd_uuid})
        except commands.SSHCommandFailed as exc:
            logging.warning('Could not destroy PBD %s: %s', pbd_uuid, exc.stdout.strip())
    sr.forget(force=True)


def destroy_nfs_sr_after_ha(pool: Pool, sr: SR) -> None:
    disable_pool_ha(pool)
    master = pool_master(pool)
    _destroy_managed_vdis(sr, master)
    try:
        sr.destroy(verify=True, force=True)
    except Exception:
        logging.warning('SR destroy failed, forgetting %s', sr.uuid, exc_info=True)
        _forget_busy_nfs_sr(sr, pool)


def destroy_ha_protected_vm(pool: Pool, vm: VM) -> None:
    disable_pool_ha(pool)
    master = pool_master(pool)
    vm.host = master
    vm.param_clear('ha-restart-priority')
    if vm.exists():
        vm.destroy(verify=True)
