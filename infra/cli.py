"""CLI

The main entrypoint for running xcpng infra script.
"""
import argparse
import logging
import sys

from infra import logger, task_update
from lib.common import HostAddress
from lib.pool import Pool

def cmd_update(args: argparse.Namespace):
    """Handles update command from args.
    """
    logger.info(f"Received host argument: '{args.host}'")
    # init related pool
    try:
        pool = Pool(args.host)
    except AssertionError as ae:
        logger.critical(ae)
        sys.exit(1)

    logger.info(f"> [{pool.master}] Begin updating target host")
    task_update.update_target(pool.master)

    logger.info(f"> [{pool.master}] Updated!")
    print(pool)


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
