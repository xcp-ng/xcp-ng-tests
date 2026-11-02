from __future__ import annotations

import pytest

import logging
import os
import shutil
import tempfile

from lib import config
from lib.commands import local_cmd
from lib.common import Defer
from lib.network import Network
from lib.vm import VM
from tests.storage import copy_vdi_via_nbd, randstream

# Requirements:
# - one XCP-ng host >= 8.3
# - the `nbdcopy` tool (from libnbd) installed on the machine running the tests

class TestNbd:
    @pytest.mark.small_vm
    def test_read_vdi_through_nbd(self, storage_test_vm: VM, defer: Defer) -> None:
        if shutil.which('nbdcopy') is None:
            pytest.fail('nbdcopy (libnbd) is not installed on the test machine')

        vm = storage_test_vm
        host = vm.host

        # Configure the management network to allow NBD traffic
        # TODO: this can be a parameter to the test, but picking the management
        #       network is easier as it's guaranteed to have IPs on all hosts
        network_uuid = host.management_network()
        network = Network(host, network_uuid)
        to_restore = network.param_get('purpose')
        network.param_clear('purpose')
        # TODO: also test NBD over TLS (purpose=nbd)
        network.param_add('purpose', 'insecure_nbd')

        def restore_purpose(network: Network) -> None:
            network.param_clear('purpose')
            network.param_add('purpose', to_restore)
        defer(lambda: restore_purpose(network))

        # Create a VDI and fill it with random data
        sr = vm.get_sr()
        vdi = sr.create_vdi(virtual_size=config.volume_size)
        defer(vdi.destroy)

        vbd = vm.connect_vdi(vdi)
        defer(lambda: vm.disconnect_vdi(vdi))
        dev = f'/dev/{vbd.param_get("device")}'
        sr.scan()

        logging.info('Writing random data to %s', dev)
        randstream(vm, f'generate {dev}')
        vm.ssh('sync')
        expected_checksum = vm.ssh(f'sha256sum {dev}').split()[0]

        # Export the VDI over NBD and verify its integrity
        fd, dest = tempfile.mkstemp(prefix=f'{vdi.uuid}-', suffix='.raw')
        os.close(fd)
        defer(lambda: os.unlink(dest))

        logging.info('Copying the VDI through NBD to %s', dest)
        copy_vdi_via_nbd(vdi, dest)

        assert os.path.getsize(dest) == vdi.get_virtual_size()
        actual_checksum = local_cmd(['sha256sum', dest]).stdout.split()[0]
        assert actual_checksum == expected_checksum, 'Data read through NBD differs from the data in the VDI'
