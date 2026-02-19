from __future__ import annotations

import pytest

import logging
from dataclasses import dataclass

from lib.common import exec_nofail, raise_errors, setup_formatted_and_mounted_disk, teardown_formatted_and_mounted_disk
from lib.host import Host
from lib.netutil import is_ipv6
from lib.pool import Pool
from lib.sr import SR
from lib.vdi import VDI
from lib.vm import VM

# explicit import for package-scope fixtures
from pkgfixtures import pool_with_saved_yum_state

from typing import Generator

GLUSTERFS_PORTS = [('24007', 'tcp'), ('49152:49251', 'tcp')]

@dataclass
class GlusterFsConfig:
    uninstall_glusterfs: bool = True

@pytest.fixture(scope='package')
def _glusterfs_config() -> GlusterFsConfig:
    return GlusterFsConfig()

def _setup_host_with_glusterfs(host: Host) -> None:
    for service in ['iptables', 'ip6tables']:
        host.ssh(f'cp /etc/sysconfig/{service} /etc/sysconfig/{service}.orig')

    host.yum_install(['glusterfs-server', 'xfsprogs'])

    iptables = 'ip6tables' if is_ipv6(host.hostname_or_ip) else 'iptables'
    for h in host.pool.hosts:
        hostname_or_ip = h.hostname_or_ip
        if hostname_or_ip != host.hostname_or_ip:
            for port, proto in GLUSTERFS_PORTS:
                host.ssh(f'{iptables} -I INPUT -p {proto} --dport {port} -s {hostname_or_ip} -j ACCEPT')

    # Make rules reboot-persistent
    for service in ['iptables', 'ip6tables']:
        host.ssh(f'service {service} save')

    host.ssh('systemctl enable --now glusterd.service')

def _uninstall_host_glusterfs(host: Host) -> None:
    errors = []
    errors += exec_nofail(lambda: host.ssh('systemctl disable --now glusterd.service'))

    # Remove any remaining gluster-related data to avoid issues in future test runs
    errors += exec_nofail(lambda: host.ssh('rm -rf /var/lib/glusterd'))

    raise_errors(errors)

def _restore_host_iptables(host: Host) -> None:
    errors = []

    iptables = 'ip6tables' if is_ipv6(host.hostname_or_ip) else 'iptables'
    for h in host.pool.hosts:
        hostname_or_ip = h.hostname_or_ip
        if hostname_or_ip != host.hostname_or_ip:
            for port, proto in GLUSTERFS_PORTS:
                errors += exec_nofail(
                    lambda: host.ssh(f'{iptables} -D INPUT -p {proto} --dport {port} -s {hostname_or_ip} -j ACCEPT')
                )

    for service in ['iptables', 'ip6tables']:
        errors += exec_nofail(
            lambda: host.ssh(f'mv /etc/sysconfig/{service}.orig /etc/sysconfig/{service}')
        )

    raise_errors(errors)

@pytest.fixture(scope='package')
def pool_without_glusterfs(host: Host) -> Generator[Pool, None, None]:
    for h in host.pool.hosts:
        if h.file_exists('/usr/sbin/glusterd'):
            raise Exception(
                f"glusterfs-server is already installed on host {h}. This should not be the case."
            )
    yield host.pool

@pytest.fixture(scope='package')
def pool_with_glusterfs(
    pool_without_glusterfs: Pool,
    pool_with_saved_yum_state: Pool,
    _glusterfs_config: GlusterFsConfig
) -> Generator[Pool, None, None]:

    def _host_rollback(host: Host) -> None:
        _uninstall_host_glusterfs(host)
        _restore_host_iptables(host)

    def _disable_yum_rollback(host: Host) -> None:
        host.saved_rollback_id = None

    pool = pool_with_saved_yum_state
    pool.exec_on_hosts_on_error_rollback(_setup_host_with_glusterfs, _host_rollback)

    yield pool

    if not _glusterfs_config.uninstall_glusterfs:
        pool.exec_on_hosts_on_error_continue(_disable_yum_rollback)
        return

    pool.exec_on_hosts_on_error_continue(_uninstall_host_glusterfs)
    pool.exec_on_hosts_on_error_continue(_restore_host_iptables)

@pytest.fixture(scope='package')
def gluster_disk(
    pool_with_unused_512B_disk: Pool,
    unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]],
    _glusterfs_config: GlusterFsConfig,
) -> Generator[None, None, None]:
    pool = pool_with_unused_512B_disk
    mountpoint = '/mnt/sr_disk'
    for h in pool.hosts:
        sr_disk = unused_512B_disks[h][0]["name"]
        setup_formatted_and_mounted_disk(h, sr_disk, 'xfs', mountpoint)

    yield

    if not _glusterfs_config.uninstall_glusterfs:
        logging.warning("<< leave fstab and keep mountpoints place for manual cleanup")
        return

    pool.exec_on_hosts_on_error_continue(
        lambda h: teardown_formatted_and_mounted_disk(h, mountpoint)
    )

def _fallback_gluster_teardown(host: Host) -> None:
    # See: https://microdevsys.com/wp/volume-delete-volume-failed-some-of-the-peers-are-down/
    # Remove all peers and bricks from the hosts volume and then stop and destroy volume.
    def teardown_for_host(h: Host) -> None:
        logging.info("< Fallback teardown on host: %s" % h)
        hosts = h.pool.hosts

        h.ssh('systemctl restart glusterd')

        gluster_cmd = 'gluster --mode=script volume remove-brick vol0 replica 1'
        for h2 in hosts:
            if h.hostname_or_ip != h2.hostname_or_ip:
                gluster_cmd = f'{gluster_cmd} {h2.hostname_or_ip}:/mnt/sr_disk/vol0/brick0'
        gluster_cmd = f'{gluster_cmd} force'
        h.ssh(gluster_cmd)

        for h2 in hosts:
            if h.hostname_or_ip != h2.hostname_or_ip:
                h.ssh(f'gluster --mode=script peer detach {h2.hostname_or_ip}')

        try:
            # Volume might already be stopped if failure happened on delete
            h.ssh('gluster --mode=script volume stop vol0')
        except Exception:
            pass

        h.ssh('gluster --mode=script volume delete vol0')

    try:
        teardown_for_host(host)
    except Exception as e:
        logging.error("< Fallback teardown failed on master: %s, attempting to teardown other hosts" % e)
        for h in host.pool.hosts[1:]:
            try:
                teardown_for_host(h)
            except Exception as e:
                logging.error("< Fallback teardown failed on host: %s with error: %s" % (h, e))
                pass

@pytest.fixture(scope='package')
def gluster_volume_started(
    host: Host,
    hostA2: Host,
    gluster_disk: None,
    _glusterfs_config: GlusterFsConfig
) -> Generator[None, None, None]:
    hosts = host.pool.hosts

    if is_ipv6(host.hostname_or_ip):
        # Configure gluster for IPv6 transport
        for h in hosts:
            h.ssh(
                'sed -i "s/#   option transport.address-family inet6/    option transport.address-family inet6/" /etc/glusterfs/glusterd.vol'  # noqa
            )
        for h in hosts:
            h.ssh('systemctl restart glusterd')

    host.ssh('mkdir -p /mnt/sr_disk/vol0/brick0')
    hostA2.ssh(f'gluster peer probe {host.hostname_or_ip}')
    for h in hosts[1:]:
        h.ssh('mkdir -p /mnt/sr_disk/vol0/brick0')
        host.ssh(f'gluster peer probe {h.hostname_or_ip}')

    logging.info(">> create and start gluster volume vol0")
    gluster_cmd = f'gluster volume create vol0 replica {str(len(hosts))}'
    for h in hosts:
        gluster_cmd = f'{gluster_cmd} {h.hostname_or_ip}:/mnt/sr_disk/vol0/brick0'

    gluster_cmd = f'{gluster_cmd} force'
    host.ssh(gluster_cmd)
    host.ssh('gluster volume set vol0 group virt')
    host.ssh('gluster volume set vol0 cluster.granular-entry-heal enable')
    host.ssh('gluster volume set vol0 features.shard-block-size 512MB')
    host.ssh('gluster volume set vol0 network.ping-timeout 5')

    host.ssh('gluster volume start vol0')

    yield

    if not _glusterfs_config.uninstall_glusterfs:
        logging.warning("<< leave gluster volume vol0 in place for manual cleanup")
        return

    logging.info("<< stop and delete gluster volume vol0")
    try:
        host.ssh('gluster --mode=script volume stop vol0')
        host.ssh('gluster --mode=script volume delete vol0')
        for h in hosts[1:]:
            host.ssh(f'gluster --mode=script peer detach {h.hostname_or_ip}')
    except Exception as e:
        logging.warning("<< Exception '%s' while tearing down gluster volume, attempting fallback teardown" % e)
        _fallback_gluster_teardown(host)

    for h in hosts:
        h.ssh('rm -rf /mnt/sr_disk/vol0')


@pytest.fixture(scope='package')
def glusterfs_device_config(host: Host) -> dict[str, str]:
    backup_servers = []
    for h in host.pool.hosts[1:]:
        backup_servers.append(h.hostname_or_ip)

    return {
        'server': '%s:/vol0' % host.hostname_or_ip,
        'backupservers': ':'.join(backup_servers)
    }

@pytest.fixture(scope='package')
def glusterfs_sr(
    host: Host,
    pool_with_glusterfs: Pool,
    gluster_volume_started: None,
    glusterfs_device_config: dict[str, str],
    _glusterfs_config: GlusterFsConfig
) -> Generator[SR, None, None]:
    """ A GlusterFS SR on first host. """
    # Create the SR
    sr = host.sr_create('glusterfs', "GlusterFS-SR-test", glusterfs_device_config, shared=True)
    yield sr
    # teardown
    try:
        sr.destroy()
    except Exception as e:
        _glusterfs_config.uninstall_glusterfs = False
        raise pytest.fail("Could not destroy glusterfs SR, leaving packages in place for manual cleanup") from e

@pytest.fixture(scope='module')
def vdi_on_glusterfs_sr(glusterfs_sr: SR) -> Generator[VDI, None, None]:
    vdi = glusterfs_sr.create_vdi('GlusterFS-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_glusterfs_sr(host: Host, glusterfs_sr: SR, vm_ref: str) -> Generator[VM, None, None]:
    vm = host.import_vm(vm_ref, sr_uuid=glusterfs_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
