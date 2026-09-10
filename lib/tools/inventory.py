"""Inventory for tools scripts."""

from __future__ import annotations

from lib.common import HostAddress
from lib.config_loader import Config

from typing import TypeAlias, TypedDict

class HostConfig(TypedDict):
    repositories: list[str]
    disabled_repositories: list[str]
    hosting_pool: HostAddress | None


HostConfigs: TypeAlias = dict[HostAddress, HostConfig]

class Inventory(TypedDict):
    hosts: HostConfigs

def inventory_from_config(config: Config) -> Inventory:
    """Create an inventory object from the config's ``[tools.update]`` and ``[hosts]`` tables."""
    default = config.tools.update
    inventory_hosts: HostConfigs = {}
    for h, config_host in config.hosts.items():
        host: HostConfig = {
            "repositories": config_host.repositories if config_host.repositories is not None else default.repositories,
            "disabled_repositories": (
                config_host.disabled_repositories if config_host.disabled_repositories is not None
                else default.disabled_repositories
            ),
            "hosting_pool": config_host.hosting_pool if config_host.hosting_pool is not None else default.hosting_pool,
        }
        inventory_hosts[h] = host

    return {
        "hosts": inventory_hosts,
    }


def into_inventory(
    hosts: list[HostAddress],
    repositories: list[str],
    hosting_pool: HostAddress | None,
    disabled_repositories: list[str] | None = None,
) -> Inventory:
    """Create an inventory object from arguments.

    Basically, it is used as compatibility when we don't want inventory from file.
    """
    inventory_hosts: HostConfigs = {}
    for h in hosts:
        host: HostConfig = {
            "repositories": repositories or [],
            "disabled_repositories": disabled_repositories or [],
            "hosting_pool": hosting_pool or None,
        }
        inventory_hosts[h] = host

    return {
        "hosts": inventory_hosts,
    }
