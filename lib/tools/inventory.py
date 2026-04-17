"""Inventory for tools scripts.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from lib.common import HostAddress

def load_inventory(inventory_path: Path) -> dict[str, dict[str, list[str]]]:
    """Create an inventory object from loaded inventory file."""
    inventory: dict[str, dict[str, list[str]]] = {}

    with open(inventory_path, "rb") as f:
        data = tomllib.load(f)

    all = data.get("all", {})
    servers = data.get("servers", [])

    for server, config in servers.items():
        repos = config.get("enablerepos", [])
        host = {
            "enablerepos": repos or all.get("enablerepos", [])
        }
        inventory[server] = host

    return inventory

def into_inventory(hosts: list[HostAddress], enablerepos: list[str]) -> dict[HostAddress, dict[str, list[str]]]:
    """Create an inventory object from arguments.

    Basically, it is used as compatibility when we don't want inventory from file.
    """
    inventory: dict[HostAddress, dict[str, list[str]]] = {}

    for h in hosts:
        host = {
            "enablerepos": enablerepos or [],
        }
        inventory[h] = host

    return inventory
