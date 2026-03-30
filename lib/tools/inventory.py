"""Inventory for tools scripts.
"""
import tomllib
from pathlib import Path

from lib.common import HostAddress

def load_inventory(inventory_path: Path) -> dict:
    """Create an inventory object from loaded inventory file."""
    inventory = {}

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

def into_inventory(hosts: list[HostAddress], enablerepos: list[str]) -> dict:
    """Create an inventory object from arguments.

    Basically, it is used as compatibility when we don't want inventory from file.
    """
    inventory = {}

    for h in hosts:
        host = {
            "enablerepos": enablerepos or [],
        }
        inventory[h] = host

    return inventory
