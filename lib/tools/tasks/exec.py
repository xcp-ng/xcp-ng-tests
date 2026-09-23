"""Exec tasks.

This module is intended for running the same command on existing remote targets,
on the master hosts first, then on the secondary hosts.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from lib.host import Host
from lib.pool import NotAMasterHostError, Pool
from lib.tools.inventory import Inventory

from .. import logger

def _exec_on_host(host: Host, command: str, dry_run: bool, reboot: bool) -> int:
    """Run `command` on `host`, log its output, optionally reboot, and return its exit code."""
    if dry_run:
        logger.info(f"[{host}] Would run: {command}")
        if reboot:
            logger.info(f"[{host}] Would reboot")
        return 0
    result = host.ssh_with_result(command)
    if result.returncode != 0:
        logger.warning(
            f"[{host}] Command exited with code {result.returncode}: {command}\n{result.stdout.strip()}"
        )
    else:
        logger.info(f"[{host}] Command output:\n{result.stdout.strip()}")
    if reboot:
        logger.info(f"[{host}] Rebooting")
        host.reboot(verify=True)
    return result.returncode

def _exec_on_hosts(hosts: list[Host], command: str, dry_run: bool, reboot: bool) -> int:
    """Run `command` on every host in parallel, returning the number of failures."""
    failures = 0
    with ThreadPoolExecutor() as executor:
        futures = {
            executor.submit(_exec_on_host, host, command, dry_run, reboot): host for host in hosts
        }
        for future in as_completed(futures):
            try:
                if future.result() != 0:
                    failures += 1
            except Exception:
                logger.error("Running command has failed!")
                failures += 1
    return failures

def exec_pools(
    inventory: Inventory,
    command: str,
    parallel: bool = False,
    dry_run: bool = False,
    reboot: bool = False,
) -> int:
    """Run a command on all hosts of pool(s).

    .. note::

        Every non-master hosts in inventory will be ignored

    *Run the command on each pool's master host declared in inventory first,
    then on the other hosts of each pool.*

    :param Inventory inventory:
        Each host (key) holds its own config data.
    :param str command:
        The command to run on every host.
    :param bool parallel:
        Run the command on the master and secondary hosts at the same time (default: False).
    :param bool dry_run:
        Only log what would be run, without running anything (default: False).
    :param bool reboot:
        Reboot each host after running the command (default: False).
    :return:
        The number of hosts on which the command failed (exit code != 0).
    """
    pools: list[Pool] = []
    for host in inventory["hosts"]:
        try:
            pools.append(Pool(host))
        except NotAMasterHostError:
            logger.warning(f"[{host}] Skipping: not a master host")

    failures = 0
    if parallel:
        # run the command on all hosts at the same time
        hosts = [h for p in pools for h in p.hosts]
        failures += _exec_on_hosts(hosts, command, dry_run, reboot)
    else:
        # run the command on the master hosts first
        failures += _exec_on_hosts([p.master for p in pools], command, dry_run, reboot)
        # then on the secondary hosts
        failures += _exec_on_hosts([h for p in pools for h in p.hosts[1:]], command, dry_run, reboot)
    return failures
