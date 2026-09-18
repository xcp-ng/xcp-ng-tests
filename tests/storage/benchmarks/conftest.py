import pytest

import logging
import os
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from lib.common import GiB
from lib.host import Host
from lib.packagemanager import Package
from lib.sr import SR
from lib.vbd import VBD
from lib.vdi import VDI, ImageFormat
from lib.vm import VM

from .helpers import FioBenchmarkCSV, load_results_from_csv

from typing import Generator

MAX_LENGTH = 64 * GiB


@pytest.fixture(scope="module")
def running_unix_vm_with_fio(running_unix_vm: VM) -> Generator[VM, None, None]:
    vm = running_unix_vm
    snapshot = vm.snapshot()

    package_manager = vm.package_manager()
    package_manager.install(Package.fio)

    yield vm

    # teardown
    try:
        snapshot.revert()
    finally:
        snapshot.destroy()


@pytest.fixture(scope="function")
def vdi_on_local_sr(host: Host, local_sr_on_hostA1: SR, image_format: ImageFormat) -> Generator[VDI, None, None]:
    sr = local_sr_on_hostA1
    vdi = sr.create_vdi("testVDI", MAX_LENGTH, image_format=image_format)
    logging.info(f">> Created VDI {vdi.uuid} of type {image_format}")

    yield vdi

    # teardown
    logging.info(f"<< Destroying VDI {vdi.uuid}")
    vdi.destroy()


@pytest.fixture(scope="function")
def plugged_vbd(vdi_on_local_sr: VDI, running_unix_vm_with_fio: VM) -> Generator[VBD, None, None]:
    vm = running_unix_vm_with_fio
    vdi = vdi_on_local_sr
    vbd = vm.create_vbd("autodetect", vdi.uuid)

    logging.info(f">> Plugging VDI {vdi.uuid} on VM {vm.uuid}")
    vbd.plug()

    yield vbd

    # teardown
    logging.info(f"<< Unplugging VDI {vdi.uuid} from VM {vm.uuid}")
    vbd.unplug()
    vbd.destroy()


@pytest.fixture(scope="module")
def local_temp_dir() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(scope="module")
def result_csv_file(local_temp_dir: Path) -> Path:
    return local_temp_dir / f"results_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.csv"


@pytest.fixture(scope="module")
def temp_dir(running_unix_vm_with_fio: VM) -> Generator[Path, None, None]:
    vm = running_unix_vm_with_fio
    tmpdir = vm.ssh("mktemp -d")

    yield Path(tmpdir)

    # teardown
    vm.ssh(f"rm -r {tmpdir}")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--benchmark-previous-results",
        action="store",
        default=None,
        help="Path/URI to previous CSV results file for comparison",
    )


@pytest.fixture(scope="session")
def prev_results(pytestconfig: pytest.Config) -> dict[str, list[FioBenchmarkCSV]]:
    csv_uri = pytestconfig.getoption("--benchmark-previous-results")
    if not csv_uri:
        return {}
    csv_path = csv_uri
    if urlparse(csv_uri).scheme != "":
        logging.info("Detected CSV path as an url")
        csv_path = f"/tmp/{uuid4()}.csv"
        urllib.request.urlretrieve(csv_uri, csv_path)
        logging.info(f"Fetching CSV file from {csv_uri} to {csv_path}")
    if not os.path.exists(csv_path):
        raise FileNotFoundError(csv_path)
    return load_results_from_csv(csv_path)
