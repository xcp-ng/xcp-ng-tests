import logging
import pytest
import urllib.request

# Explicitly import package-scoped fixtures (see explanation in pkgfixtures.py)
from pkgfixtures import host_with_saved_yum_state
# Requirements:
# From --hosts parameter:
# - host(A1): any master host of a pool, with access to XCP-ng RPM repositories and reports.xcp-ng.org.

@pytest.fixture(scope="session")
def extra_pkgs(host):
    version = host.xcp_version_short
    url = f"https://reports.xcp-ng.org/{version}/extra_installable.txt"

    logging.info(f"Getting extra packages from {url}")
    response = urllib.request.urlopen(url)
    return response.read().decode('utf-8').splitlines()
