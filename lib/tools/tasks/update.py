"""Update tasks.

This module is intended for performing update actions on existing remote targets.
"""

from lib.host import Host

from .. import logger

from typing import List

def update_host(host: Host, enablerepos: list[str] = []):
    """Updates the target host.

    An helper function that wraps update tasks on specific host.

    :param :py:class:`lib.host.Host` host:
        Target host to update.
    :param list[str] enablerepos:
        Repositories to enable when updating.
    """
    logger.info(f"[{host}] Updating...")

    host.yum_clean_metadata()
    host.yum_update(enablerepos=enablerepos)
    # Everything's ok, just reboot
    host.reboot(verify=True)

    logger.info(f"[{host}] Updated successfully!")
