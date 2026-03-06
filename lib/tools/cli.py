"""CLI Entrypoint

The main entrypoint for running tools script.
"""
import argparse
import logging

from lib.common import HostAddress
from lib.tools import logger
from lib.tools.tasks.update import update_all

def _command_update(args):
    update_all(args.hosts, args.repos)


def cli():
    parser = argparse.ArgumentParser(
        description="Tools that help developers for running recurrent tasks on their xcpng sandbox."
    )
    parser.add_argument("-d", "--debug", action="store_true", default=False, help="Enable debug level")

    subparsers = parser.add_subparsers(required=True, metavar="COMMAND")

    # subparser - command: update
    subparser_cmd_update = subparsers.add_parser(
        name="update",
        description="Run update tasks on target(s)",
        help="Run update tasks on target(s)",
    )
    subparser_cmd_update.add_argument(
        "hosts", type=HostAddress, metavar="HOST", nargs="+", help="Hostname(s) or ip address(es) of target(s)"
    )
    subparser_cmd_update.add_argument(
        "--enablerepo",
        metavar="REPO",
        # nargs="?",
        action="append",
        dest="repos",
        help="Enable one or more repositories",
    )
    subparser_cmd_update.set_defaults(func=_command_update)

    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    args.func(args)
