import pytest
from lib.common import SSHCommandFailed

# See: https://gluster.readthedocs.io/en/release-3.7.0-1/Troubleshooting/troubleshootingFAQ/
# ('49152', 'tcp') for brick0 and ('49153', 'tcp') for brick0 after `test_volume_stopped`
# because the port number is increased
GLUSTERFS_PORTS = [('24007', 'tcp'), ('49152', 'tcp'), ('49153', 'tcp')]

def _setup_host_with_glusterfs(host):
    assert not host.file_exists('/usr/sbin/glusterd'), \
        "glusterfs-server must not be installed on the host at the beginning of the tests"
    host.yum_save_state()
    host.yum_install(['glusterfs-server'])
    host.ssh(['systemctl', 'enable', '--now', 'glusterd.service'])

    for h in host.pool.hosts:
        hostname_or_ip = h.hostname_or_ip
        if hostname_or_ip != host.hostname_or_ip:
            for port, proto in GLUSTERFS_PORTS:
                host.ssh(
                    ['iptables', '-I', 'INPUT', '-p', proto, '--dport', port, '-s', hostname_or_ip, '-j', 'ACCEPT'])

    # Make rules reboot-persistent
    for service in ['iptables', 'ip6tables']:
        host.ssh(['cp', '/etc/sysconfig/%s' % service, '/etc/sysconfig/%s.orig' % service])
        host.ssh(['service', service, 'save'])

def _teardown_host_with_glusterfs(host):
    errors = []
    try:
        host.ssh(['systemctl', 'disable', '--now', 'glusterd.service'])
    except SSHCommandFailed as e:
        print("WARNING: %s" % e)
        errors.append(e)
    host.yum_restore_saved_state()

    for h in host.pool.hosts:
        hostname_or_ip = h.hostname_or_ip
        if hostname_or_ip != host.hostname_or_ip:
            for port, proto in GLUSTERFS_PORTS:
                try:
                    host.ssh(
                        ['iptables', '-D', 'INPUT', '-p', proto, '--dport', port, '-s', hostname_or_ip, '-j', 'ACCEPT'])
                except SSHCommandFailed as e:
                    print("WARNING: %s" % e)
                    errors.append(e)

    for service in ['iptables', 'ip6tables']:
        try:
            host.ssh(['mv', '/etc/sysconfig/%s.orig' % service, '/etc/sysconfig/%s' % service])
        except SSHCommandFailed as e:
            print("WARNING: %s" % e)
            errors.append(e)

    if len(errors) > 0:
        raise Exception("\n".join(repr(e) for e in errors))

@pytest.fixture(scope='session')
def pool_with_glusterfs(host):
    setup_errors = []
    for h in host.pool.hosts:
        try:
            _setup_host_with_glusterfs(h)
        except Exception as e:
            print("WARNING: %s" % e)
            setup_errors.append(e)
            pass
    if len(setup_errors) > 0:
        raise Exception("\n".join(repr(e) for e in setup_errors))

    yield
    teardown_errors = []
    for h in host.pool.hosts:
        try:
            _teardown_host_with_glusterfs(h)
        except Exception as e:
            teardown_errors.append(e)
            pass
    if len(teardown_errors) > 0:
        raise Exception("\n".join(repr(e) for e in teardown_errors))

@pytest.fixture(scope='session')
def gluster_disk(host, sr_disk_for_all_hosts):
    sr_disk = sr_disk_for_all_hosts
    device = '/dev/' + sr_disk
    hosts = host.pool.hosts
    for h in hosts:
        print(">> Format sr_disk %s and mount it on host %s" % (sr_disk, h))
        h.ssh(['mkfs.ext4', '-F', device])
        h.ssh(['rm', '-rf', '/mnt/sr_disk']) # Remove any existing leftover to ensure rmdir will not fail in teardown
        h.ssh(['mkdir', '-p', '/mnt/sr_disk'])
        h.ssh(['cp', '-f', '/etc/fstab', '/etc/fstab.orig'])
        h.ssh(['echo', '%s /mnt/sr_disk ext4 defaults 0 0' % device, '>>/etc/fstab'])
        h.ssh(['mount', '/mnt/sr_disk'])
    yield
    for h in hosts:
        print("<< Restore fstab and unmount /mnt/sr_disk on host %s" % h)
        h.ssh(['cp', '-f', '/etc/fstab.orig', '/etc/fstab'])
        h.ssh(['umount', '/mnt/sr_disk'])
        h.ssh(['rmdir', '/mnt/sr_disk'])

def _fallback_gluster_teardown(host):
    # See: https://microdevsys.com/wp/volume-delete-volume-failed-some-of-the-peers-are-down/
    # Remove all peers and bricks from the hosts volume and then stop and destroy volume.
    def teardown_for_host(h):
        print("< Fallback teardown on host: %s" % h)
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
        except Exception as e:
            pass

        h.ssh(['gluster', '--mode=script', 'volume', 'delete', 'vol0'])

    try:
        teardown_for_host(host)
    except Exception as e:
        print("< Fallback teardown failed on master: %s, attempting to teardown other hosts" % e)
        for h in host.pool.hosts[1:]:
            try:
                teardown_for_host(h)
            except Exception as e:
                print("< Fallback teardown failed on host: %s with error: %s" % (h, e))
                pass

@pytest.fixture(scope='session')
def gluster_volume_started(host, hostA2, gluster_disk):
    hosts = host.pool.hosts

    host.ssh(['mkdir', '-p', '/mnt/sr_disk/vol0/brick0'])
    hostA2.ssh(['gluster', 'peer', 'probe', host.hostname_or_ip])
    for h in hosts[1:]:
        h.ssh(['mkdir', '-p', '/mnt/sr_disk/vol0/brick0'])
        host.ssh(['gluster', 'peer', 'probe', h.hostname_or_ip])

    print(">> create and start gluster volume vol0")
    gluster_cmd = ['gluster', 'volume', 'create', 'vol0', 'replica', str(len(hosts))]
    for h in hosts:
        gluster_cmd.append('%s:/mnt/sr_disk/vol0/brick0' % h.hostname_or_ip)

    gluster_cmd.append('force')
    host.ssh(gluster_cmd)

    host.ssh(['gluster', 'volume', 'start', 'vol0'])
    yield
    print("<< stop and delete gluster volume vol0")
    try:
        host.ssh(['gluster', '--mode=script', 'volume', 'stop', 'vol0'])
        host.ssh(['gluster', '--mode=script', 'volume', 'delete', 'vol0'])
        for h in hosts[1:]:
            host.ssh(['gluster', '--mode=script', 'peer', 'detach', h.hostname_or_ip])
    except Exception as e:
        print("<< Exception '%s' while tearing down gluster volume, attempting fallback teardown" % e)
        _fallback_gluster_teardown(host)

    for h in hosts:
        h.ssh(['rm', '-rf', '/mnt/sr_disk/vol0'])


@pytest.fixture(scope='session')
def glusterfs_device_config(host):
    backup_servers = []
    for h in host.pool.hosts[1:]:
        backup_servers.append(h.hostname_or_ip)

    return {
        'server': '%s:/vol0' % host.hostname_or_ip,
        'backupservers': ':'.join(backup_servers)
    }

@pytest.fixture(scope='session')
def glusterfs_sr(host, pool_with_glusterfs, gluster_volume_started, glusterfs_device_config):
    """ A GlusterFS SR on first host. """
    # Create the SR
    sr = host.sr_create('glusterfs', "GlusterFS-SR", glusterfs_device_config, shared=True)
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vm_on_glusterfs_sr(host, glusterfs_sr, vm_ref):
    print(">> ", end='')
    vm = host.import_vm(vm_ref, sr_uuid=glusterfs_sr.uuid)
    yield vm
    # teardown
    print("<< Destroy VM")
    vm.destroy(verify=True)
