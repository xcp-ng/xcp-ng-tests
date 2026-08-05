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
from lib.tools.tasks.clean import clean_pools
from lib.tools.tasks.exec import exec_pools
from lib.tools.tasks.update import update_pools

def _command_update(args: argparse.Namespace) -> None:
    if args.inventory:
        inventory = load_inventory(args.inventory)
    else:
        inventory = into_inventory(args.hosts, args.repos, args.hosting_pool, disabled_repositories=args.disablerepos)

    update_pools(inventory, reboot=args.reboot, parallel=args.parallel)


def _command_clean(args: argparse.Namespace) -> None:
    if args.inventory:
        inventory = load_inventory(args.inventory)
    else:
        inventory = into_inventory(args.hosts, [], args.hosting_pool)

    clean_pools(inventory, dry_run=args.dry_run)


def _command_exec(args: argparse.Namespace) -> int:
    if args.inventory:
        inventory = load_inventory(args.inventory)
    else:
        inventory = into_inventory(args.hosts, [], None)

    command = " ".join(args.command)
    return exec_pools(inventory, command, parallel=args.parallel, dry_run=args.dry_run, reboot=args.reboot)


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
    subparser_cmd_update.add_argument(
        "-x", "--disablerepo",
        metavar="REPO",
        action="append",
        dest="disablerepos",
        help="repositories to disable when updating",
    )
    subparser_cmd_update.add_argument(
        "-P",
        "--hosting-pool",
        type=HostAddress,
        help="Address (hostname|ip) of hosting pool's master host (nested context)",
    )
    subparser_cmd_update.add_argument(
        "--no-reboot",
        action="store_false",
        dest="reboot",
        default=True,
        help="Don't reboot after update operation",
    )
    subparser_cmd_update.add_argument(
        "--parallel",
        action="store_true",
        default=False,
        help="Update the master and secondary hosts at the same time",
    )
    subparser_cmd_update.set_defaults(func=_command_update)

    # subparser - command: clean
    subparser_cmd_clean = subparsers.add_parser(
        name="clean",
        description="Remove all VMs, snapshorts and VDIs on local storage from target pools",
        help="Remove all VMs, snapshorts and VDIs on local storage from target pools",
    )
    cmd_clean_excl_grp = subparser_cmd_clean.add_mutually_exclusive_group(required=True)
    cmd_clean_excl_grp.add_argument(
        "-H",
        "--hosts",
        type=HostAddress,
        metavar="HOST",
        nargs="+",
        help="Address (hostname|ip) of the master host in pool",
    )
    cmd_clean_excl_grp.add_argument("-i", "--inventory", type=Path, help="Use an hosts inventory file")
    subparser_cmd_clean.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        default=False,
        help="Only display what would be removed, without deleting anything",
    )
    subparser_cmd_clean.set_defaults(func=_command_clean)

    # subparser - command: exec
    subparser_cmd_exec = subparsers.add_parser(
        name="exec",
        description="Run the same command on all hosts of target pools",
        help="Run the same command on all hosts of target pools",
    )
    cmd_exec_excl_grp = subparser_cmd_exec.add_mutually_exclusive_group(required=True)
    cmd_exec_excl_grp.add_argument(
        "-H",
        "--hosts",
        type=HostAddress,
        metavar="HOST",
        nargs="+",
        help="Address (hostname|ip) of the master host in pool",
    )
    cmd_exec_excl_grp.add_argument("-i", "--inventory", type=Path, help="Use an hosts inventory file")
    subparser_cmd_exec.add_argument(
        "--parallel",
        action="store_true",
        default=False,
        help="Run the command on the master and secondary hosts at the same time",
    )
    subparser_cmd_exec.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        default=False,
        help="Only log what would be run, without running anything",
    )
    subparser_cmd_exec.add_argument(
        "-r",
        "--reboot",
        action="store_true",
        default=False,
        help="Reboot each host after running the command",
    )
    subparser_cmd_exec.add_argument(
        "command",
        metavar="COMMAND",
        nargs="+",
        help="Command to run on every host",
    )
    subparser_cmd_exec.set_defaults(func=_command_exec)

    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    exit_code = args.func(args)
    if exit_code:
        raise SystemExit(exit_code)
