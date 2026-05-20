"""Inventory for tools scripts."""

from __future__ import annotations

import tomllib
from pathlib import Path

from lib.common import HostAddress

from typing import TypeAlias, TypedDict

class HostConfig(TypedDict):
    repositories: list[str]
    hosting_pool: HostAddress | None


HostConfigs: TypeAlias = dict[HostAddress, HostConfig]

class Inventory(TypedDict):
    hosts: HostConfigs

def load_inventory(inventory_path: Path) -> Inventory:
    """Create an inventory object from loaded inventory file."""
    with open(inventory_path, "rb") as f:
        data = tomllib.load(f)

    default = data.get("default", {})
    hosts = data.get("hosts", [])

    inventory_hosts: HostConfigs = {}
    for h, config in hosts.items():
        repos = config.get("repositories", [])
        hosting_pool = config.get("hosting_pool", None)
        if hosting_pool is None:
            hosting_pool = default.get("hosting_pool", None)
        host: HostConfig = {
            "repositories": repos or default.get("repositories", []),
            "hosting_pool": hosting_pool,
        }
        inventory_hosts[h] = host

    return {
        "hosts": inventory_hosts,
    }


def into_inventory(hosts: list[HostAddress], repositories: list[str], hosting_pool: HostAddress) -> Inventory:
    """Create an inventory object from arguments.

    Basically, it is used as compatibility when we don't want inventory from file.
    """
    inventory_hosts: HostConfigs = {}
    for h in hosts:
        host: HostConfig = {
            "repositories": repositories or [],
            "hosting_pool": hosting_pool or None,
        }
        inventory_hosts[h] = host

    return {
        "hosts": inventory_hosts,
    }
