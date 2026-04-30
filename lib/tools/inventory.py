"""Inventory for tools scripts."""

from __future__ import annotations

import tomllib
from pathlib import Path

from lib.common import HostAddress

from typing import TypeAlias, TypedDict

class HostConfig(TypedDict):
    repositories: list[str]


Inventory: TypeAlias = dict[HostAddress, HostConfig]


def load_inventory(inventory_path: Path) -> Inventory:
    """Create an inventory object from loaded inventory file."""
    inventory: Inventory = {}

    with open(inventory_path, "rb") as f:
        data = tomllib.load(f)

    all = data.get("all", {})
    hosts = data.get("hosts", [])

    for server, config in hosts.items():
        repos = config.get("repositories", [])
        host: HostConfig = {"repositories": repos or all.get("repositories", [])}
        inventory[server] = host

    return inventory


def into_inventory(hosts: list[HostAddress], repositories: list[str]) -> Inventory:
    """Create an inventory object from arguments.

    Basically, it is used as compatibility when we don't want inventory from file.
    """
    inventory: Inventory = {}

    for h in hosts:
        host: HostConfig = {"repositories": repositories or []}
        inventory[h] = host

    return inventory
