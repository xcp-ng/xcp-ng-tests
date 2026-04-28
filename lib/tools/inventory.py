"""Inventory for tools scripts."""

from __future__ import annotations

import tomllib
from pathlib import Path

from lib.common import HostAddress

from typing import TypeAlias, TypedDict

class Server(TypedDict):
    enablerepos: list[str]


Inventory: TypeAlias = dict[HostAddress, Server]


def load_inventory(inventory_path: Path) -> Inventory:
    """Create an inventory object from loaded inventory file."""
    inventory: Inventory = {}

    with open(inventory_path, "rb") as f:
        data = tomllib.load(f)

    all = data.get("all", {})
    servers = data.get("servers", [])

    for server, config in servers.items():
        repos = config.get("enablerepos", [])
        host: Server = {"enablerepos": repos or all.get("enablerepos", [])}
        inventory[server] = host

    return inventory


def into_inventory(hosts: list[HostAddress], enablerepos: list[str]) -> Inventory:
    """Create an inventory object from arguments.

    Basically, it is used as compatibility when we don't want inventory from file.
    """
    inventory: Inventory = {}

    for h in hosts:
        host: Server = {"enablerepos": enablerepos or []}
        inventory[h] = host

    return inventory
