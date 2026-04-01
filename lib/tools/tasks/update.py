"""Update tasks.

This module is intended for performing update actions on existing remote targets.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from lib.pool import NotAMasterHostError, Pool

from .. import logger

def update_pools(inventory: dict) -> None:
    """Updates hosts in pool(s).

    .. note:: Host must be a master

        Every non-master hosts will be ignored

    :param dict inventory:
        Each host (key) holds its own config data (values, eg: `enablerepos`).
    """
    logger.debug(f"Inventory: {inventory}")
    # init related pools
    pools = []
    for h in inventory:
        try:
            p = Pool(h)
            pools.append(p)
        except NotAMasterHostError:
            logger.warning(f"[{h}] Skipping: not a master host")

    with ThreadPoolExecutor() as executor:
        for p in pools:
            executor.submit(p.master.update, inventory[p.master.hostname_or_ip]["enablerepos"])
