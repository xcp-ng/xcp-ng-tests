"""Update Infrastructure

This module contains helpful functions to update
xcp-ng infrastructure.
"""
from lib.host import Host

def update_target(host: Host):
    """Update the target.

    Performs several operations to clean and update
    remote target.
    """
    # TODO: performance and output improvements
    #     Following operations run for a long time
    #     Without 'debug mode', it is not possible
    #     to see whether operations are running or not
    host.clean_metadata()
    host.install_updates()
