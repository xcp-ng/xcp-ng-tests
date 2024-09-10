import logging
import pytest

from lib.common import safe_split

@pytest.fixture(scope="session")
def enabled_pgpu_uuid(host):
    pgpu_uuids = safe_split(host.xe("pgpu-list", {"host-uuid": host.uuid}, minimal=True), ',')

    pgpu_uuid = None
    for uuid in pgpu_uuids:
        pci_uuid = host.xe("pgpu-param-get", {"uuid": uuid, "param-name": "pci-uuid"})
        dom0_access = host.xe("pci-get-dom0-access-status", {"uuid": pci_uuid})
        if dom0_access == "enabled":
            pgpu_uuid = uuid
            break

    if pgpu_uuid is None:
        pytest.skip("This test requires a PGPU to hide from dom0")

    yield pgpu_uuid

    # Put PGPU back in initial state
    pci_uuid = host.xe("pgpu-param-get", {"uuid": pgpu_uuid, "param-name": "pci-uuid"})
    if host.xe("pci-get-dom0-access-status", {"uuid": pci_uuid}) != "enabled":
        host.xe("pci-enable-dom0-access", {"uuid": pci_uuid})
        if host.xe("pci-get-dom0-access-status", {"uuid": pci_uuid}) != "enabled":
            host.reboot(verify=True)

@pytest.fixture(scope="session")
def enabled_pci_uuid(host, enabled_pgpu_uuid):
    pci_uuid = host.xe("pgpu-param-get", {"uuid": enabled_pgpu_uuid, "param-name": "pci-uuid"})
    return pci_uuid
