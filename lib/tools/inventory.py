"""Inventory for tools scripts."""

from __future__ import annotations

import tomllib
from pathlib import Path

from lib.common import HostAddress

from typing import TypeAlias, TypedDict

class HostConfig(TypedDict):
    repositories: list[str]


HostConfigs: TypeAlias = dict[HostAddress, HostConfig]

class Inventory(TypedDict):
    hosts: HostConfigs
    parent: HostAddress | None

def load_inventory(inventory_path: Path) -> Inventory:
    """Create an inventory object from loaded inventory file."""
    with open(inventory_path, "rb") as f:
        data = tomllib.load(f)

    all = data.get("all", {})
    hosts = data.get("hosts", [])

    inventory_hosts: HostConfigs = {}
    for h, config in hosts.items():
        repos = config.get("repositories", [])
        host: HostConfig = {"repositories": repos or all.get("repositories", [])}
        inventory_hosts[h] = host

    return {
        "hosts": inventory_hosts,
        "parent": data.get("parent", None),
    }


def into_inventory(hosts: list[HostAddress], repositories: list[str], parent: HostAddress) -> Inventory:
    """Create an inventory object from arguments.

    Basically, it is used as compatibility when we don't want inventory from file.
    """
    inventory_hosts: HostConfigs = {}
    for h in hosts:
        host: HostConfig = {"repositories": repositories or []}
        inventory_hosts[h] = host

    return {
        "hosts": inventory_hosts,
        "parent": parent,
    }
