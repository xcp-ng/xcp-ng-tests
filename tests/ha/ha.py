from __future__ import annotations

import logging

import lib.commands as commands
from lib.common import strtobool, wait_for
from lib.host import Host
from lib.pool import Pool
from lib.sr import SR
from lib.vdi import VDI
from lib.vm import VM
from tests.ha.iptables import clear_persistent_rules
from tests.ha.moonshot import power_on_host

from typing import Any, Callable

# Waiting for HA to act: enable/disable, fence, master takeover, VM restart.
HA_FAILOVER_TIMEOUT_SECS = 10 * 60
# Waiting for a host to boot and xapi to start.
HA_RECOVERY_TIMEOUT_SECS = 15 * 60
# How long a scenario lets HA run before checking that nothing happened.
HA_SETTLE_SECS = 2 * 60

# Errors returned while xapi restarts, or while a new master takes over.
TRANSIENT_XAPI_ERRORS = (
    'still live',
    'connection refused',
    'unable to contact server',
    'lost connection to the server',
    'cannot connect to redo log',
    'still initialising',
    'still booting',
    'cannot be contacted',
    'could not be contacted',
    'missing table',
    'invalid object reference',
)


def best_effort(fn: Callable[..., object], *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
    try:
        fn(*args, **kwargs)
    except Exception:
        logging.warning(f'{fn.__name__} failed during cleanup', exc_info=True)


def host_boot_id(host: Host) -> str:
    return host.ssh('cat /proc/sys/kernel/random/boot_id')


def assert_not_rebooted(boot_ids: dict[Host, str]) -> None:
    for h, boot_id in boot_ids.items():
        assert h.is_ssh_reachable(), f'{h} is unreachable: fenced by HA?'
        assert host_boot_id(h) == boot_id, f'{h} rebooted: fenced by HA'


def wait_for_fence_reboot(host: Host, boot_id_before: str) -> None:
    def rebooted() -> bool:
        if not host.is_ssh_reachable():
            return False
        try:
            return host_boot_id(host) != boot_id_before
        except commands.SSHCommandFailed:
            return False

    wait_for(
        rebooted,
        f'Wait for {host} to come back from HA fence (new boot id)',
        timeout_secs=HA_RECOVERY_TIMEOUT_SECS,
        retry_delay_secs=10,
    )


def wait_for_xapi(fn: Callable[[], bool], msg: str, timeout_secs: int) -> None:
    """wait_for() that keeps retrying while xapi is not ready."""
    def attempt() -> bool:
        try:
            return fn()
        except commands.SSHCommandFailed as exc:
            matched = next((e for e in TRANSIENT_XAPI_ERRORS if e in exc.stdout.lower()), None)
            if matched is None:
                raise
            logging.info(f'xapi not ready ({matched!r}), retrying')
            return False

    wait_for(attempt, msg, timeout_secs=timeout_secs, retry_delay_secs=5)


def wait_for_pool_master(pool: Pool, host: Host) -> None:
    wait_for_xapi(
        lambda: pool.is_live_master(host),
        f'Wait for {host} to be pool master',
        HA_FAILOVER_TIMEOUT_SECS,
    )
    pool.master = host


def wait_for_live_master(pool: Pool) -> Host:
    """Find the live pool master. Several hosts acting as master (split brain) is an error."""
    wait_for_xapi(
        lambda: any(pool.is_live_master(h) for h in pool.hosts),
        f'Wait for a live master in pool {pool.uuid}',
        HA_FAILOVER_TIMEOUT_SECS,
    )
    masters = [h for h in pool.hosts if pool.is_live_master(h)]
    assert len(masters) == 1, f'Expected one pool master, found: {", ".join(str(h) for h in masters)}'
    pool.master = masters[0]
    logging.info(f'Live pool master: {pool.master}')
    return pool.master


def _static_vdis(host: Host) -> list[str]:
    """VDIs that dom0 attaches at boot: the HA statefile and metadata while HA is on."""
    # grep not cat: the vdi-uuid files have no trailing newline.
    return host.ssh('grep -h . /etc/xensource/static-vdis/*/vdi-uuid 2>/dev/null; true').split()


def assert_pool_ready(pool: Pool) -> None:
    """Fail fast on a pool left broken by a previous run: shared SR operations would hang."""
    assert not pool.is_ha_enabled(), f'HA still enabled on pool {pool.uuid}: previous cleanup failed'
    for host in pool.hosts:
        assert host.is_enabled(), f'{host} is not enabled in the pool: previous cleanup failed'
        # Left over when HA was disabled while this host was down (or emergency-disabled):
        # xapi does not release them, and they keep the heartbeat SR busy.
        assert not _static_vdis(host), f'{host} still has HA static VDIs attached: repair the pool by hand'


def disable_pool_ha(pool: Pool) -> None:
    # No emergency fallback: if pool-ha-disable fails, the pool needs a manual fix.
    # A stale HA metadata static-vdi makes it fail up to xapi 26.1.19.
    if pool.is_ha_enabled():
        pool.disable_ha()
        wait_for_xapi(
            lambda: not pool.is_ha_enabled(),
            f'Wait for HA disabled on pool {pool.uuid}',
            HA_FAILOVER_TIMEOUT_SECS,
        )


def restore_pool_after_ha(pool: Pool) -> None:
    wait_for_live_master(pool)
    disable_pool_ha(pool)

    for member in pool.hosts:
        if member.uuid == pool.master.uuid:
            continue

        def enabled() -> bool:
            if not member.is_ssh_reachable():
                return False
            if not member.is_enabled():
                logging.info(f'Re-enable host {member}')
                pool.master.xe('host-enable', {'uuid': member.uuid})
            return member.is_enabled()

        wait_for_xapi(enabled, f'Wait for host {member} enabled in pool', HA_RECOVERY_TIMEOUT_SECS)


def restore_original_master(pool: Pool) -> None:
    """Make hosts[0] the master again. Call it after restore_pool_after_ha()."""
    preferred = pool.hosts[0]
    wait_for_live_master(pool)
    if pool.master.uuid == preferred.uuid:
        return
    logging.info(f'Restore original master {preferred} (current master {pool.master})')
    pool.designate_new_master(preferred)


def _power_on_and_wait_ssh(host: Host) -> None:
    power_on_host(host.hostname_or_ip)
    host.wait_for_host_up(timeout_secs=HA_RECOVERY_TIMEOUT_SECS)
    host.wait_for_ssh_reachable(timeout_secs=HA_RECOVERY_TIMEOUT_SECS)


def _is_healthy_slave(host: Host) -> bool:
    # With HA on, a host is live only once it is back in the HA liveset
    # A fenced host can stay enabled.
    return (
        host.is_ssh_reachable()
        and not host.is_master()
        and host.is_enabled()
        and strtobool(host.param_get('host-metrics-live'))
    )


def rejoin(
    pool: Pool,
    host: Host,
    *,
    after_ssh: Callable[[Host], None] | None = None,
) -> None:
    """Power host on if needed and wait until it is back in the pool. Stops if it does not rejoin."""
    _power_on_and_wait_ssh(host)
    if after_ssh is not None:
        after_ssh(host)
    master = wait_for_live_master(pool)
    try:
        wait_for_xapi(
            lambda: host.uuid == master.uuid or _is_healthy_slave(host),
            f'Wait for {host} to rejoin the pool',
            HA_RECOVERY_TIMEOUT_SECS,
        )
    except TimeoutError as exc:
        raise AssertionError(f'{host} did not rejoin the pool: repair the pool by hand') from exc


def recover_from_fence(
    pool: Pool,
    failed: Host,
    survivor: Host,
    *,
    after_ssh: Callable[[Host], None] | None = None,
) -> None:
    # Disable HA only once failed is back: xapi releases the statefile and metadata VDIs
    # on every host that is up and skips the others.
    rejoin(pool, failed, after_ssh=after_ssh)
    restore_pool_after_ha(pool)
    assert survivor.is_master(), f'{survivor} should still be master after recovery'
    assert _is_healthy_slave(failed), f'{failed} should be an enabled slave after recovery'


def cleanup_ha_scenario(pool: Pool) -> None:
    """Brings every host back before disabling HA, so xapi releases the HA VDIs everywhere."""
    for host in pool.hosts:
        # A no-op on hosts without persistent rules.
        best_effort(rejoin, pool, host, after_ssh=clear_persistent_rules)
    best_effort(restore_pool_after_ha, pool)


def prepare_pool_for_ha(pool: Pool) -> tuple[Host, Host]:
    """Check the pool is ready for an HA scenario and return (master, slave)."""
    assert len(pool.hosts) == 2, f'HA scenarios need a 2-host pool, got {len(pool.hosts)}'
    master = wait_for_live_master(pool)
    slave = next(h for h in pool.hosts if h.uuid != master.uuid)

    for member in pool.hosts:
        assert member.is_ssh_reachable(), f'{member} must be reachable before HA scenario'
        member.wait_for_xapi_enabled(timeout_secs=HA_RECOVERY_TIMEOUT_SECS)
    assert_pool_ready(pool)
    return master, slave


def destroy_ha_vdis(pool: Pool, sr: SR) -> None:
    """Destroy the statefile and metadata VDIs that pool-ha-disable leaves on the SR."""
    # Never destroy one that a host still has attached.
    assert_pool_ready(pool)
    sr.scan()
    for label in ('Statefile for HA', 'Metadata for HA'):
        for vdi_uuid in sr.vdi_uuids(managed=True, name_label=label):
            logging.info(f'Destroy leftover {label} VDI {vdi_uuid}')
            VDI(vdi_uuid, sr=sr).destroy()


def start_ha_vm(vm: VM, on_host: Host) -> None:
    vm.param_set('ha-restart-priority', 'restart')

    def halted() -> bool:
        if vm.is_running():
            vm.shutdown(force=True)
        return not vm.is_running()

    wait_for_xapi(halted, f'Wait for VM {vm.uuid} halted', HA_FAILOVER_TIMEOUT_SECS)
    vm.start(on=on_host.uuid)
    vm.wait_for_os_booted()


def enable_pool_ha(pool: Pool, nfs_sr: SR) -> None:
    max_fail = pool.master.xe('pool-ha-compute-max-host-failures-to-tolerate')
    assert max_fail != '0', 'Pool cannot tolerate any host failure with current capacity'
    pool.enable_ha(nfs_sr, max_fail)
    wait_for(
        pool.is_ha_enabled,
        f'Wait for HA enabled on pool {pool.uuid}',
        timeout_secs=HA_FAILOVER_TIMEOUT_SECS,
    )
    # Proves HA can restart the VM on the other host.
    wait_for(
        lambda: int(pool.param_get('ha-plan-exists-for') or 0) >= 1,
        'Wait for an HA restart plan covering one host failure',
        timeout_secs=HA_FAILOVER_TIMEOUT_SECS,
    )


def wait_for_vm_on_host(
    vm: VM,
    on_host: Host,
    msg: str,
    *,
    booted: bool = True,
) -> None:
    # The previous vm.host may have been fenced.
    vm.host = on_host

    def check() -> bool:
        try:
            return vm.is_running_on_host(on_host)
        except commands.SSHCommandFailed as exc:
            logging.debug(f'VM state check failed: {exc}')
            return False

    wait_for(check, msg, timeout_secs=HA_FAILOVER_TIMEOUT_SECS, retry_delay_secs=5)
    if booted:
        vm.wait_for_os_booted()


def wait_for_failover(pool: Pool, vm: VM, survivor: Host, reason: str) -> None:
    wait_for_pool_master(pool, survivor)
    wait_for_vm_on_host(vm, survivor, f'Wait for VM to restart on {survivor} {reason}')
