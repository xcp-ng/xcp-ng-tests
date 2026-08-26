"""Update tasks.

This module is intended for performing update actions on existing remote targets.
"""
from __future__ import annotations

import re
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager

from lib.host import Host
from lib.pool import NotAMasterHostError, Pool
from lib.tools.inventory import HostConfig, Inventory
from lib.tools.tasks.snapshot import create_snapshots

from .. import logger

def _capture_packages(pools: list[Pool]) -> dict[Host, set[str]]:
    """Snapshot the installed packages of every host in the pools."""
    return {h: set(h.packages()) for p in pools for h in p.hosts}

def _filter_packages(pkgs: set[str]) -> set[str]:
    return {p for p in pkgs if not p.startswith("gpg-pubkey-")}

def _format_packages(pkgs: list[str]) -> str:
    return "\n".join(f"  - {p}" for p in pkgs)

def _nevra_to_nvra(nevra: str) -> str:
    return re.sub(r'-\d+:', '-', nevra)

def _downgrade_command(host: Host, pkgs: set[str]) -> str:
    """Return a `yum downgrade` command for the given installed packages."""
    specs = " ".join(_nevra_to_nvra(p) for p in sorted(pkgs))
    names = host.ssh(f"rpm -q --qf '%{{NAME}} ' {specs}").split()
    return "yum downgrade -y " + " ".join(names)

def _report_updated(before: dict[Host, set[str]], after: dict[Host, set[str]]) -> None:
    """Log a summary of the packages that were updated on each host."""
    updated = {
        h: _filter_packages(after[h] - pkgs) for h, pkgs in before.items()
    }
    common_updated = set.intersection(*updated.values()) if updated else set()

    if not common_updated:
        logger.info("No packages were updated on any host.")
        return
    logger.info(
        f"Updated packages on all hosts ({len(common_updated)}):\n"
        f"{_format_packages(sorted(common_updated))}"
    )
    for h, pkgs in updated.items():
        extra = sorted(pkgs - common_updated)
        if extra:
            logger.info(
                f"Additional packages on [{h}] ({len(extra)}):\n"
                f"{_format_packages(extra)}"
            )

def _check_packages_available(
    pools: list[Pool],
    inventory_hosts: dict[str, HostConfig],
    packages: dict[Host, set[str]],
) -> None:
    """Warn about installed packages not available in the selected repositories."""
    unavailable: dict[Host, set[str]] = {}
    for p in pools:
        master_cfg = inventory_hosts[p.master.hostname_or_ip]
        repoquery_cmd = "repoquery --all"
        for r in master_cfg["disabled_repositories"]:
            repoquery_cmd += f" --disablerepo={r}"
        for r in master_cfg["repositories"]:
            repoquery_cmd += f" --enablerepo={r}"
        with _pinned_updates_repo(p.master):
            available = set(p.master.ssh(repoquery_cmd).splitlines())
        for h in p.hosts:
            pkgs = _filter_packages(packages[h]) - available
            if pkgs:
                unavailable[h] = pkgs

    if not unavailable:
        logger.info("All installed packages are available in the selected repositories.")
        return
    common = set.intersection(*unavailable.values())
    lines = []
    downgrades = {h: _downgrade_command(h, pkgs) for h, pkgs in unavailable.items()}
    if common:
        lines.append(
            f"Installed packages not available in the selected repositories on all hosts "
            f"({len(common)}):\n{_format_packages(sorted(common))}"
        )
    for h, pkgs in unavailable.items():
        extra = sorted(pkgs - common)
        if extra:
            lines.append(
                f"Additional packages on [{h}] ({len(extra)}):\n{_format_packages(extra)}"
            )
    common_downgrade = next(iter(downgrades.values()))
    if set(downgrades.values()) == {common_downgrade}:
        lines.append(f"Downgrade command on all hosts:\n  {common_downgrade}")
    else:
        for h, cmd in downgrades.items():
            lines.append(f"Downgrade command on [{h}]:\n  {cmd}")
    logger.warning("\n".join(lines))

def _check_consistency(packages: dict[Host, set[str]]) -> None:
    """Warn if not all hosts end up with the same set of packages."""
    common_set = set.intersection(*packages.values())
    inconsistent = {
        h: _filter_packages(p) - _filter_packages(common_set)
        for h, p in packages.items() if _filter_packages(p) != _filter_packages(common_set)
    }
    if inconsistent:
        lines = [
            f"Not all hosts have the same set of packages "
            f"(reference: common set of {len(packages)} hosts):"
        ]
        for h, extra_pkgs in inconsistent.items():
            lines.append(f"  [{h}] additional packages:\n{_format_packages(sorted(extra_pkgs))}")
        logger.warning("\n".join(lines))

@contextmanager
def _pinned_updates_repo(host: Host) -> Generator[None]:
    """Temporarily drop the mirrors.xcp-ng.org baseurls, restoring the file on exit."""
    repo_file = '/etc/yum.repos.d/xcp-ng.repo'
    backup_file = f'{repo_file}.bak'
    logger.info(f"[{host}] Removing mirrors.xcp-ng.org from {repo_file}")
    host.ssh(f'cp -f {repo_file} {backup_file}')
    host.ssh(f"sed -i 's|http://mirrors\\.xcp-ng\\.org/[^ ]*[ ]*||g' {repo_file}")
    try:
        yield
    finally:
        logger.info(f"[{host}] Restoring {repo_file}")
        host.ssh(f'mv -f {backup_file} {repo_file}')

def _update_host(host: Host, enablerepos: list[str], disablerepos: list[str] = [],
                 reboot: bool = True) -> None:
    """Update a host, with the mirrors.xcp-ng.org baseurl removed during the update."""
    with _pinned_updates_repo(host):
        host.update(enablerepos, disablerepos=disablerepos, reboot=reboot)

def update_pools(inventory: Inventory, reboot: bool = True, parallel: bool = False) -> None:
    """Updates hosts in pool(s).

    .. note::

        Every non-master hosts in inventory will be ignored

    *Update each pool's master host declared in inventory first, then, update other hosts for each pool.*

    :param dict inventory:
        Each host (key) holds its own config data (values, eg: `enablerepos`).
    :param bool reboot:
        Choose to reboot or not after update (default: True).
    :param bool parallel:
        Update the master and secondary hosts at the same time (default: False).
    """
    logger.debug(f"Inventory: {inventory}")
    inventory_hosts = inventory["hosts"]
    # init related pools
    pools: list[Pool] = []
    nested_hosts: dict[str, list[Host]] = {}
    for host in inventory_hosts:
        try:
            p = Pool(host)
            pools.append(p)
            hosting_pool = inventory_hosts[host]["hosting_pool"]
            if hosting_pool is not None:
                if nested_hosts.get(hosting_pool) is not None:
                    nested_hosts[hosting_pool].extend([h for h in p.hosts if h.is_nested])
                else:
                    nested_hosts[hosting_pool] = [h for h in p.hosts if h.is_nested]
        except NotAMasterHostError:
            logger.warning(f"[{host}] Skipping: not a master host")

    before_packages = _capture_packages(pools)

    # update master hosts
    with ThreadPoolExecutor() as executor:
        future_hosts = {executor.submit(
            _update_host,
            p.master,
            inventory_hosts[p.master.hostname_or_ip]["repositories"],
            disablerepos=inventory_hosts[p.master.hostname_or_ip]["disabled_repositories"],
            reboot=reboot,
        ): p.master for p in pools}
        if parallel:
            # update other hosts at the same time as the master hosts
            for p in pools:
                # omit first item because it is the pool's master
                for h in p.hosts[1:]:
                    # repos are the same as for the master host
                    repos = inventory_hosts[p.master.hostname_or_ip]["repositories"]
                    disablerepos = inventory_hosts[p.master.hostname_or_ip]["disabled_repositories"]
                    future_hosts[executor.submit(_update_host, h, repos, disablerepos=disablerepos, reboot=reboot)] = h
        for future in as_completed(future_hosts):
            updated_host = future_hosts[future]
            try:
                future.result()
            except Exception as exc:
                logger.error(f"Updating pool has failed! The host {updated_host} cannot be updated.")
                logger.info(
                    "*** Due to previous error, the pool updating task will stop. "
                    "Waiting for running updates to finish if any. ***"
                )
                raise exc

    if not parallel:
        # update other hosts
        with ThreadPoolExecutor() as executor:
            future_other_hosts = {}
            for p in pools:
                # omit first item because it is the pool's master
                for h in p.hosts[1:]:
                    # repos are the same as for the master host
                    repos = inventory_hosts[p.master.hostname_or_ip]["repositories"]
                    disablerepos = inventory_hosts[p.master.hostname_or_ip]["disabled_repositories"]
                    future_other_hosts[executor.submit(
                        _update_host, h, repos, disablerepos=disablerepos, reboot=reboot)] = h
            for future in as_completed(future_other_hosts):
                other_host = future_other_hosts[future]
                try:
                    future.result()
                except Exception as exc:
                    logger.error(f"Updating pool has failed! The host {other_host} cannot be updated.")
                    logger.info(
                        "*** Due to previous error, the pool updating task will stop. "
                        "Waiting for running updates to finish if any. ***"
                    )
                    raise exc

    after_packages = _capture_packages(pools)
    _report_updated(before_packages, after_packages)
    _check_consistency(after_packages)
    _check_packages_available(pools, inventory_hosts, after_packages)

    # Snapshot creation
    for hosting_pool, nested in nested_hosts.items():
        pool = Pool(hosting_pool) # mandatory for getting an host instance
        vm_uuids = [h.get_system_uuid() for h in nested]
        create_snapshots(pool.master, vm_uuids)
