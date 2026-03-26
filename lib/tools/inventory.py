"""Inventory for tools scripts."""

from __future__ import annotations

import tomllib
from pathlib import Path

from lib.common import HostAddress

from typing import TypeAlias, TypedDict

class Server(TypedDict):
    enablerepos: list[str]
    nested: bool


Servers: TypeAlias = dict[HostAddress, Server]

class Inventory(TypedDict):
    hosts: Servers
    parent: HostAddress | None

def load_inventory(inventory_path: Path) -> Inventory:
    """Create an inventory object from loaded inventory file."""
    with open(inventory_path, "rb") as f:
        data = tomllib.load(f)

    all = data.get("all", {})
    servers = data.get("servers", [])

    inventory_hosts: Servers = {}
    for server, config in servers.items():
        nested = config.get("nested", None)
        # We can't use 'False' as fallback value here, because 'False' is falsy...
        if nested is None:
            nested = all.get("nested", False)

        repos = config.get("enablerepos", [])
        host: Server = {
            "enablerepos": repos or all.get("enablerepos", []),
            "nested": nested,
        }
        inventory_hosts[server] = host

    return {
        "hosts": inventory_hosts,
        "parent": data.get("parent", None),
    }


def into_inventory(
    hosts: list[HostAddress], enablerepos: list[str], parent: HostAddress, nested: bool
) -> Inventory:
    """Create an inventory object from arguments.

    Basically, it is used as compatibility when we don't want inventory from file.
    """
    inventory_hosts: Servers = {}
    for h in hosts:
        host: Server = {
            "enablerepos": enablerepos or [],
            "nested": nested or False,
        }
        inventory_hosts[h] = host

    return {
        "hosts": inventory_hosts,
        "parent": parent,
    }
