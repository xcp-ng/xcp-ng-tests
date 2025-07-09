import pytest

import logging

from lib.common import exec_nofail, raise_errors, setup_formatted_and_mounted_disk, teardown_formatted_and_mounted_disk
from lib.netutil import is_ipv6

# explicit import for package-scope fixtures
from pkgfixtures import pool_with_saved_yum_state

GLUSTERFS_PORTS = [('24007', 'tcp'), ('49152:49251', 'tcp')]

def _setup_host_with_glusterfs(host):
    for service in ['iptables', 'ip6tables']:
        host.ssh(['cp', '/etc/sysconfig/%s' % service, '/etc/sysconfig/%s.orig' % service])

    host.yum_install(['glusterfs-server', 'xfsprogs'])

    iptables = 'ip6tables' if is_ipv6(host.hostname_or_ip) else 'iptables'
    for h in host.pool.hosts:
        hostname_or_ip = h.hostname_or_ip
        if hostname_or_ip != host.hostname_or_ip:
            for port, proto in GLUSTERFS_PORTS:
                host.ssh(
                    [iptables, '-I', 'INPUT', '-p', proto, '--dport', port, '-s', hostname_or_ip, '-j', 'ACCEPT'])

    # Make rules reboot-persistent
    for service in ['iptables', 'ip6tables']:
        host.ssh(['service', service, 'save'])

    host.ssh(['systemctl', 'enable', '--now', 'glusterd.service'])

def _teardown_host_with_glusterfs(host):
    errors = []
    errors += exec_nofail(lambda: host.ssh(['systemctl', 'disable', '--now', 'glusterd.service']))

    # Remove any remaining gluster-related data to avoid issues in future test runs
    errors += exec_nofail(lambda: host.ssh(['rm', '-rf', '/var/lib/glusterd']))

    iptables = 'ip6tables' if is_ipv6(host.hostname_or_ip) else 'iptables'
    for h in host.pool.hosts:
        hostname_or_ip = h.hostname_or_ip
        if hostname_or_ip != host.hostname_or_ip:
            for port, proto in GLUSTERFS_PORTS:
                errors += exec_nofail(
                    lambda: host.ssh(
                        [iptables, '-D', 'INPUT', '-p', proto, '--dport', port, '-s', hostname_or_ip, '-j', 'ACCEPT']
                    )
                )

    for service in ['iptables', 'ip6tables']:
        errors += exec_nofail(
            lambda: host.ssh(['mv', '/etc/sysconfig/%s.orig' % service, '/etc/sysconfig/%s' % service])
        )

    raise_errors(errors)

@pytest.fixture(scope='package')
def pool_without_glusterfs(host):
    for h in host.pool.hosts:
        if h.file_exists('/usr/sbin/glusterd'):
            raise Exception(
                f"glusterfs-server is already installed on host {h}. This should not be the case."
            )
    yield host.pool

@pytest.fixture(scope='package')
def pool_with_glusterfs(pool_without_glusterfs, pool_with_saved_yum_state):
    pool = pool_with_saved_yum_state
    pool.exec_on_hosts_on_error_rollback(_setup_host_with_glusterfs, _teardown_host_with_glusterfs)
    yield pool
    pool.exec_on_hosts_on_error_continue(_teardown_host_with_glusterfs)

@pytest.fixture(scope='package')
def gluster_disk(host, sr_disk_for_all_hosts):
    sr_disk = sr_disk_for_all_hosts
    mountpoint = '/mnt/sr_disk'
    for h in host.pool.hosts:
        setup_formatted_and_mounted_disk(h, sr_disk, 'xfs', mountpoint)
    yield
    host.pool.exec_on_hosts_on_error_continue(
        lambda h: teardown_formatted_and_mounted_disk(h, mountpoint)
    )

def _fallback_gluster_teardown(host):
    # See: https://microdevsys.com/wp/volume-delete-volume-failed-some-of-the-peers-are-down/
    # Remove all peers and bricks from the hosts volume and then stop and destroy volume.
    def teardown_for_host(h):
        logging.info("< Fallback teardown on host: %s" % h)
        hosts = h.pool.hosts

        h.ssh(['systemctl', 'restart', 'glusterd'])

        gluster_cmd = ['gluster', '--mode=script', 'volume', 'remove-brick', 'vol0', 'replica', '1']
        for h2 in hosts:
            if h.hostname_or_ip != h2.hostname_or_ip:
                gluster_cmd.append('%s:/mnt/sr_disk/vol0/brick0' % h2.hostname_or_ip)
        gluster_cmd.append('force')
        h.ssh(gluster_cmd)

        for h2 in hosts:
            if h.hostname_or_ip != h2.hostname_or_ip:
                h.ssh(['gluster', '--mode=script', 'peer', 'detach', h2.hostname_or_ip])

        try:
            # Volume might already be stopped if failure happened on delete
            h.ssh(['gluster', '--mode=script', 'volume', 'stop', 'vol0'])
        except Exception:
            pass

        h.ssh(['gluster', '--mode=script', 'volume', 'delete', 'vol0'])

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
def gluster_volume_started(host, hostA2, gluster_disk):
    hosts = host.pool.hosts

    if is_ipv6(host.hostname_or_ip):
        # Configure gluster for IPv6 transport
        for h in hosts:
            h.ssh([
                'sed',
                '-i',
                '"s/#   option transport.address-family inet6/    option transport.address-family inet6/"',
                '/etc/glusterfs/glusterd.vol'
            ])
        for h in hosts:
            h.ssh(['systemctl', 'restart', 'glusterd'])

    host.ssh(['mkdir', '-p', '/mnt/sr_disk/vol0/brick0'])
    hostA2.ssh(['gluster', 'peer', 'probe', host.hostname_or_ip])
    for h in hosts[1:]:
        h.ssh(['mkdir', '-p', '/mnt/sr_disk/vol0/brick0'])
        host.ssh(['gluster', 'peer', 'probe', h.hostname_or_ip])

    logging.info(">> create and start gluster volume vol0")
    gluster_cmd = ['gluster', 'volume', 'create', 'vol0', 'replica', str(len(hosts))]
    for h in hosts:
        gluster_cmd.append('%s:/mnt/sr_disk/vol0/brick0' % h.hostname_or_ip)

    gluster_cmd.append('force')
    host.ssh(gluster_cmd)
    host.ssh(['gluster', 'volume', 'set', 'vol0', 'group', 'virt'])
    host.ssh(['gluster', 'volume', 'set', 'vol0', 'cluster.granular-entry-heal', 'enable'])
    host.ssh(['gluster', 'volume', 'set', 'vol0', 'features.shard-block-size', '512MB'])
    host.ssh(['gluster', 'volume', 'set', 'vol0', 'network.ping-timeout', '5'])

    host.ssh(['gluster', 'volume', 'start', 'vol0'])
    yield
    logging.info("<< stop and delete gluster volume vol0")
    try:
        host.ssh(['gluster', '--mode=script', 'volume', 'stop', 'vol0'])
        host.ssh(['gluster', '--mode=script', 'volume', 'delete', 'vol0'])
        for h in hosts[1:]:
            host.ssh(['gluster', '--mode=script', 'peer', 'detach', h.hostname_or_ip])
    except Exception as e:
        logging.warning("<< Exception '%s' while tearing down gluster volume, attempting fallback teardown" % e)
        _fallback_gluster_teardown(host)

    for h in hosts:
        h.ssh(['rm', '-rf', '/mnt/sr_disk/vol0'])


@pytest.fixture(scope='package')
def glusterfs_device_config(host):
    backup_servers = []
    for h in host.pool.hosts[1:]:
        backup_servers.append(h.hostname_or_ip)

    return {
        'server': '%s:/vol0' % host.hostname_or_ip,
        'backupservers': ':'.join(backup_servers)
    }

@pytest.fixture(scope='package')
def glusterfs_sr(host, pool_with_glusterfs, gluster_volume_started, glusterfs_device_config):
    """ A GlusterFS SR on first host. """
    # Create the SR
    sr = host.sr_create('glusterfs', "GlusterFS-SR-test", glusterfs_device_config, shared=True)
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_glusterfs_sr(glusterfs_sr):
    vdi = glusterfs_sr.create_vdi('GlusterFS-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_glusterfs_sr(host, glusterfs_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=glusterfs_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
