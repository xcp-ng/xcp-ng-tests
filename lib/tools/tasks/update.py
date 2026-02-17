"""Update tasks.

This module is intended for performing update actions on existing remote targets.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from lib.pool import Pool

from .. import logger

def update_all(inventory: dict[str, dict[str, list[str]]]) -> None:
    """Updates all master (primary) hosts.

    .. note:: Host must be a master

        Throws error if hosts are not master (primary).

    :param dict inventory:
        Each host (key) holds its own config data (values).
    """
    logger.debug(f"Inventory: {inventory}")
    # init related pools
    pools = [Pool(h) for h in inventory]

    with ThreadPoolExecutor() as executor:
        for p in pools:
            executor.submit(p.master.update, inventory[p.master.hostname_or_ip]["enablerepos"])
