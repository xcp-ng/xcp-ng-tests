import logging
import pytest
import urllib.request

@pytest.fixture(scope="session")
def extra_pkgs(host):
    version = host.xcp_version
    url = f"https://reports.xcp-ng.org/{version}/extra_installable.txt"

    logging.info(f"Getting extra packages from {url}")
    response = urllib.request.urlopen(url)
    return response.read().decode('utf-8').splitlines()
