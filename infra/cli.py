"""CLI

The main entrypoint for running xcpng infra script.
"""
import argparse
import logging

from infra import logger
from lib.common import HostAddress

def cmd_update(args: argparse.Namespace):
    """Handles update command from args.
    """
    logger.info(f"Received host argument: '{args.host}'")
    pass


def cli():
    parser = argparse.ArgumentParser(description="Helpfully manages xcp-ng infrastructure resources.")
    parser.add_argument("--debug", action="store_true", default=False, help="Changes logs level to debug.")
    subparsers = parser.add_subparsers(required=True, metavar="COMMAND")

    # subparser - command: update
    update_cmd_subparser = subparsers.add_parser(name="update",
                                                 description="Performs update operations on target.",
                                                 help="Performs update operations on target.")
    update_cmd_subparser.add_argument("host",
                                      type=HostAddress,
                                      metavar="HOST",
                                      help="hostname or ip address of target.")

    update_cmd_subparser.set_defaults(func=cmd_update)

    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    args.func(args)
