"""Update tasks.

This module is intended for performing update actions on existing remote targets.
"""

from lib.host import Host
from lib.tools import logger

from typing import List

def update_host(host: Host, enablerepos: List[str] = []):
    """Updates the target.

    Updating a remote host target has the following workflow:

    * Cleans cached metadata
    * Updates packages (with optional repositories enabled or not)
    * Reboots (verifies whether the host is up or not)

    :param :py:class:`lib.host.Host` host: Target host to update.
    :param List[str] enablerepos: Extra repo(s) to enable (default: []).
    """
    logger.info(f"[{host}] Begin updating target host")

    host.yum_clean_metadata()
    host.yum_update(enablerepos=enablerepos)
    # Everything's ok, just reboot
    host.reboot(verify=True)

    logger.info(f"[{host}] Updated!")
