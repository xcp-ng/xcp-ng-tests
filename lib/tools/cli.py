"""CLI Entrypoint

The main entrypoint for running tools script.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from lib.common import HostAddress
from lib.config_loader import base_config_dict, load_config
from lib.tools import logger
from lib.tools.inventory import into_inventory, inventory_from_config
from lib.tools.tasks.clean import clean_pools
from lib.tools.tasks.exec import exec_pools
from lib.tools.tasks.migrate import migrate_data_py
from lib.tools.tasks.update import update_pools

def _command_update(args: argparse.Namespace) -> None:
    if args.hosts:
        inventory = into_inventory(args.hosts, args.repos, args.hosting_pool, disabled_repositories=args.disablerepos)
    else:
        inventory = inventory_from_config(load_config(override=args.config))
        for host in inventory["hosts"].values():
            if args.repos:
                host["repositories"] = args.repos
            if args.disablerepos:
                host["disabled_repositories"] = args.disablerepos
            if args.hosting_pool:
                host["hosting_pool"] = args.hosting_pool
        if not inventory["hosts"]:
            logger.warning("No hosts defined: pass -H/--hosts or define them in the config file")

    update_pools(inventory, reboot=args.reboot, parallel=args.parallel)


def _command_clean(args: argparse.Namespace) -> int:
    if args.hosts:
        inventory = into_inventory(args.hosts, [], None)
    else:
        inventory = inventory_from_config(load_config(override=args.config))
        if not inventory["hosts"]:
            logger.warning("No hosts defined: pass -H/--hosts or define them in the config file")

    return clean_pools(inventory, dry_run=args.dry_run)


def _command_exec(args: argparse.Namespace) -> int:
    if args.hosts:
        inventory = into_inventory(args.hosts, [], None)
    else:
        inventory = inventory_from_config(load_config(override=args.config))
        if not inventory["hosts"]:
            logger.warning("No hosts defined: pass -H/--hosts or define them in the config file")

    command = " ".join(args.command)
    return exec_pools(inventory, command, parallel=args.parallel, dry_run=args.dry_run, reboot=args.reboot)


def _command_migrate(args: argparse.Namespace) -> int:
    return migrate_data_py(data_py=args.data_py, output=args.output, force=args.force,
                           include_defaults=args.all)


def _command_dump_config(args: argparse.Namespace) -> int:
    import json

    from lib.config_dump import colorize_toml, remove_defaults, render_toml

    config = load_config(override=args.config).model_dump(by_alias=True)
    if not args.all:
        config = remove_defaults(config, base_config_dict())
    if args.json:
        print(json.dumps(config, indent=2, ensure_ascii=False))
    else:
        out = render_toml(config, with_schema=False)
        use_color = args.color if args.color is not None else (
            sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        )
        print(colorize_toml(out) if use_color else out)
    return 0


def cli() -> None:
    parser = argparse.ArgumentParser(
        description="Tools that help developers for running recurrent tasks on their XCP-ng sandbox."
    )
    parser.add_argument("-d", "--debug", action="store_true", default=False, help="Enable debug level")
    parser.add_argument(
        "-c", "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="Config overlay: a .toml file path or profile name (default: config.default.toml or XCPNG_CONFIG)",
    )

    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "-c", "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="Config overlay: a .toml file path or profile name (default: config.default.toml or XCPNG_CONFIG)",
    )

    subparsers = parser.add_subparsers(required=True, metavar="COMMAND")

    # subparser - command: update
    subparser_cmd_update = subparsers.add_parser(
        name="update",
        parents=[common_parser],
        description="Run update tasks on target pools",
        help="Run update tasks on target pools",
    )
    subparser_cmd_update.add_argument(
        "-H",
        "--hosts",
        type=HostAddress,
        metavar="HOST",
        nargs="+",
        help="Address (hostname|ip) of the master host in pool (default: hosts defined in the config file)",
    )
    subparser_cmd_update.add_argument(
        "-e", "--enablerepo",
        metavar="REPO",
        action="append",
        dest="repos",
        help="repositories to enable when updating (overrides the config file)",
    )
    subparser_cmd_update.add_argument(
        "-x", "--disablerepo",
        metavar="REPO",
        action="append",
        dest="disablerepos",
        help="repositories to disable when updating (overrides the config file)",
    )
    subparser_cmd_update.add_argument(
        "-P",
        "--hosting-pool",
        type=HostAddress,
        help="Address (hostname|ip) of hosting pool's master host (nested context, overrides the config file)",
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
        parents=[common_parser],
        description="Remove all VMs, snapshorts and VDIs on local storage from target pools",
        help="Remove all VMs, snapshorts and VDIs on local storage from target pools",
    )
    subparser_cmd_clean.add_argument(
        "-H",
        "--hosts",
        type=HostAddress,
        metavar="HOST",
        nargs="+",
        help="Address (hostname|ip) of the master host in pool (default: hosts defined in the config file)",
    )
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
        parents=[common_parser],
        description="Run the same command on all hosts of target pools",
        help="Run the same command on all hosts of target pools",
    )
    subparser_cmd_exec.add_argument(
        "-H",
        "--hosts",
        type=HostAddress,
        metavar="HOST",
        nargs="+",
        help="Address (hostname|ip) of the master host in pool (default: hosts defined in the config file)",
    )
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

    # subparser - command: migrate-data-py
    subparser_cmd_migrate = subparsers.add_parser(
        name="migrate-data-py",
        parents=[common_parser],
        description="Convert a legacy data.py file to a TOML config file",
        help="Convert a legacy data.py file to a TOML config file",
    )
    subparser_cmd_migrate.add_argument(
        "data_py",
        metavar="DATA_PY",
        nargs="?",
        default=None,
        help="Path to the legacy data.py file (default: data.py in the repo root)",
    )
    subparser_cmd_migrate.add_argument(
        "--output",
        default="config.default.toml",
        help="Output file name (default: config.default.toml in the repo root)",
    )
    subparser_cmd_migrate.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite the output file if it already exists",
    )
    subparser_cmd_migrate.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="Keep the values that are the same as in config.toml instead of writing a delta-only overlay",
    )
    subparser_cmd_migrate.set_defaults(func=_command_migrate)

    # subparser - command: dump-config
    subparser_cmd_dump = subparsers.add_parser(
        name="dump-config",
        parents=[common_parser],
        description="Print the fully resolved configuration to stdout",
        help="Print the fully resolved configuration to stdout",
    )
    subparser_cmd_dump.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Dump as JSON instead of TOML",
    )
    subparser_cmd_dump.add_argument(
        "--color",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Colorize TOML output (default: auto, only when stdout is a TTY)",
    )
    subparser_cmd_dump.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="Include the values that are the same as in config.toml",
    )
    subparser_cmd_dump.set_defaults(func=_command_dump_config)

    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    exit_code = args.func(args)
    if exit_code:
        raise SystemExit(exit_code)
