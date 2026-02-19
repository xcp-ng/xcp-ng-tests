import pytest

import logging
import urllib.request

from lib.host import Host

# Explicitly import package-scoped fixtures (see explanation in pkgfixtures.py)
from pkgfixtures import host_with_saved_yum_state

@pytest.fixture(scope="session")
def extra_pkgs(host: Host) -> list[str]:
    version = host.xcp_version_short
    url = f"https://reports.xcp-ng.org/{version}/extra_installable.txt"

    logging.info(f"Getting extra packages from {url}")
    response = urllib.request.urlopen(url)
    return response.read().decode('utf-8').splitlines()
