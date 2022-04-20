import logging
import pytest

from lib.common import wait_for

@pytest.mark.reboot
def test(host):
    logging.info("Check for updates")
    if not host.has_updates():
        pytest.skip("No updates available for the host. Skipping.")

    host.install_updates()
    host.restart_toolstack()
    wait_for(host.is_enabled, "Wait for host enabled")
    host.reboot(verify=True)
    logging.info("Check for updates again")
    assert not host.has_updates()
