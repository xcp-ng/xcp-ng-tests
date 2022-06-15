GROUP_NAME = 'linstor_group'

def create_linstor_sr(label, hosts):
    master = hosts[0]

    return master.sr_create('linstor', label, {
        'hosts': ','.join([host.hostname() for host in hosts]),
        'group-name': GROUP_NAME,
        'redundancy': str(len(hosts)),
        'provisioning': 'thick'
    }, shared=True)
