"""CLI

The main entrypoint for running xcpng infra script.
"""
import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor

from infra import logger, task_update
from lib.common import HostAddress
from lib.pool import Pool

def cmd_update(args: argparse.Namespace):
    """Handles update command from args.
    """
    logger.info(f"Received host argument: '{args.hosts}'")
    # init related pools
    try:
        pools = [Pool(h) for h in args.hosts]
        logger.info("Preparing Pools...")
    except AssertionError as ae:
        logger.critical(ae)
        sys.exit(1)

    # Run in parallel
    if len(pools) > 1:
        logger.info("Multiple targets to update")
        hosts = [p.master for p in pools]
        with ThreadPoolExecutor() as executor:
            executor.map(task_update.update_target, hosts)
    else:
        task_update.update_target(pools[0].master)

    print(pools)


def cli():
    parser = argparse.ArgumentParser(description="Helpfully manages xcp-ng infrastructure resources.")
    parser.add_argument("--debug", action="store_true", default=False, help="Changes logs level to debug.")
    subparsers = parser.add_subparsers(required=True, metavar="COMMAND")

    # subparser - command: update
    update_cmd_subparser = subparsers.add_parser(name="update",
                                                 description="Performs update operations on target(s).",
                                                 help="Performs update operations on target(s).")
    update_cmd_subparser.add_argument("hosts",
                                      type=HostAddress,
                                      metavar="HOST",
                                      nargs="+",
                                      help="hostname(s) or ip address(es) of target(s).")

    update_cmd_subparser.set_defaults(func=cmd_update)

    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    args.func(args)
