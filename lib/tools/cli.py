"""CLI Entrypoint

The main entrypoint for running tools script.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from lib.common import HostAddress
from lib.tools import logger
from lib.tools.inventory import into_inventory, load_inventory
from lib.tools.tasks.update import update_pools

def _command_update(args: argparse.Namespace) -> None:
    if args.inventory:
        inventory = load_inventory(args.inventory)
    else:
        inventory = into_inventory(args.hosts, args.repos)

    update_pools(inventory)


def cli() -> None:
    parser = argparse.ArgumentParser(
        description="Tools that help developers for running recurrent tasks on their XCP-ng sandbox."
    )
    parser.add_argument("-d", "--debug", action="store_true", default=False, help="Enable debug level")

    subparsers = parser.add_subparsers(required=True, metavar="COMMAND")

    # subparser - command: update
    subparser_cmd_update = subparsers.add_parser(
        name="update",
        description="Run update tasks on target pools",
        help="Run update tasks on target pools",
    )
    cmd_update_excl_grp = subparser_cmd_update.add_mutually_exclusive_group(required=True)
    cmd_update_excl_grp.add_argument(
        "-H",
        "--hosts",
        type=HostAddress,
        metavar="HOST",
        nargs="+",
        help="Address (hostname|ip) of the master host in pool",
    )
    cmd_update_excl_grp.add_argument("-i", "--inventory", type=Path, help="Use an hosts inventory file")
    subparser_cmd_update.add_argument(
        "-e", "--enablerepo",
        metavar="REPO",
        action="append",
        dest="repos",
        help="repositories to enable when updating",
    )
    subparser_cmd_update.set_defaults(func=_command_update)

    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    args.func(args)
