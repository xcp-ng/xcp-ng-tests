import logging
import os
from urllib.request import urlretrieve

from lib.host import Host

# These tests are basic tests meant to check the update process in air-gapped environment.
#
# Requirements:
# - a host running XCP-ng 8.3.
# - with no access to internet
# - TODO: a snapshot to restore the host to a state without the offline repositories, to integrate when #226
#   will be merged

REPOS = ['base', 'linstor', 'updates']


def air_gapped_download(url: str, host: Host):
    logging.debug(f"downloading {url}")
    tmppath = f'/tmp/{os.path.basename(url)}'
    urlretrieve(url, tmppath)
    host.scp(tmppath, tmppath)
    os.remove(tmppath)


def test_air_gapped_update(host: Host):
    # get the required files and install the offline repositories
    air_gapped_download(
        'https://raw.githubusercontent.com/xcp-ng/xcp/refs/heads/master/scripts/setup_offline_xcpng_repos', host
    )
    host.ssh(['chmod', 'a+x', '/tmp/setup_offline_xcpng_repos'])
    for repo in REPOS:
        archive = f'xcpng-8_3-offline-{repo}-latest.tar'
        air_gapped_download(f'https://repo.vates.tech/xcp-ng/offline/8/8.3/{archive}', host)
        host.ssh(['/tmp/setup_offline_xcpng_repos', f'/tmp/{archive}'])
        host.ssh(['rm', '-f', f'/tmp/{archive}'])
    repolist = host.ssh(['yum', 'repolist', '-q'])
    # ensure the repos are installed
    for repo in REPOS:
        assert f'xcp-ng-{repo}' in repolist
    # run the actual update
    host.ssh(['yum', '-y', 'update'])
    # restart the XAPI toolstack
    host.ssh(['xe-toolstack-restart'])
