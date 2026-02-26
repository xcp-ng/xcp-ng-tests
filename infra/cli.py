"""CLI

The main entrypoint for running xcpng infra script.
"""
import argparse

from lib.common import HostAddress

def cli():
    parser = argparse.ArgumentParser(description="Helpfully manages xcp-ng infrastructure resources.")
    subparsers = parser.add_subparsers(required=True, metavar="COMMAND")

    # subparser - command: update
    update_cmd_subparser = subparsers.add_parser(name="update",
                                                 description="Performs update operations on target.",
                                                 help="Performs update operations on target.")
    update_cmd_subparser.add_argument("host",
                                      type=HostAddress,
                                      metavar="HOST",
                                      help="hostname or ip address of target.")

    args = parser.parse_args()

    print(f"Received host argument: '{args.host}'")
