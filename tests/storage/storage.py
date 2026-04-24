from __future__ import annotations

import logging
from dataclasses import dataclass

from lib import config
from lib.commands import SSHCommandFailed
from lib.common import QCOW2_MAX, VHD_MAX, Defer, GiB, MiB, TiB, strtobool, wait_for
from lib.host import Host
from lib.sr import SR
from lib.vdi import VDI, ImageFormat
from lib.vm import VM

from typing import Literal

MAX_VDI_SIZE: dict[ImageFormat, int] = {'qcow2': QCOW2_MAX, 'vhd': VHD_MAX}

def try_to_create_sr_with_missing_device(sr_type, label, host) -> None:
    try:
        host.sr_create(sr_type, label, {}, verify=True)
    except SSHCommandFailed as e:
        assert e.stdout == (
            'Error code: SR_BACKEND_FAILURE_90\nError parameters: , '
            + 'The request is missing the device parameter,'
        ), 'Bad error, current: {}'.format(e.stdout)
        return
    assert False, 'SR creation should not have succeeded!'

def cold_migration_then_come_back(vm: VM, prov_host: Host, dest_host: Host, dest_sr: SR) -> None:
    """ Storage migration of a shutdown VM, then migrate it back. """
    prov_sr = vm.get_sr()
    vdi_name: str | None = None
    integrity_check = not vm.is_windows
    dev = ""
    spans: list[StreamSpan] = []

    if integrity_check:
        # the vdi will be destroyed with the vm
        vdi = prov_sr.create_vdi(virtual_size=config.volume_size)
        vdi_name = vdi.name()
        vbd = vm.connect_vdi(vdi)
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()
        install_randstream(vm)
        dev = f'/dev/{vbd.param_get("device")}'
        spans = partially_populate_device(vm, dev, config.volume_size)
        validate_partially_populated_device(vm, dev, spans)
        vm.shutdown(verify=True)

    assert vm.is_halted()

    # Move the VM to another host of the pool
    vm.migrate(dest_host, dest_sr)
    wait_for(lambda: vm.all_vdis_on_sr(dest_sr), "Wait for all VDIs on destination SR")

    # Start VM to make sure it works
    vm.start(on=dest_host.uuid)
    vm.wait_for_os_booted()
    if integrity_check:
        vm.wait_for_vm_running_and_ssh_up()
        validate_partially_populated_device(vm, dev, spans)
    vm.shutdown(verify=True)

    # Migrate it back to the provenance SR
    vm.migrate(prov_host, prov_sr)
    wait_for(lambda: vm.all_vdis_on_sr(prov_sr), "Wait for all VDIs back on provenance SR")

    # Start VM to make sure it works
    vm.start(on=prov_host.uuid)
    vm.wait_for_os_booted()
    if integrity_check:
        vm.wait_for_vm_running_and_ssh_up()
        validate_partially_populated_device(vm, dev, spans)
    vm.shutdown(verify=True)

    if vdi_name is not None:
        vm.destroy_vdi_by_name(vdi_name)

def live_storage_migration_then_come_back(vm: VM, prov_host: Host, dest_host: Host, dest_sr: SR) -> None:
    prov_sr = vm.get_sr()
    vdi_name: str | None = None
    integrity_check = not vm.is_windows
    dev = ""
    spans: list[StreamSpan] = []
    vbd = None

    if integrity_check:
        vdi = prov_sr.create_vdi(virtual_size=config.volume_size)
        vdi_name = vdi.name()
        vbd = vm.connect_vdi(vdi)

    # start VM
    vm.start(on=prov_host.uuid)
    vm.wait_for_os_booted()
    if integrity_check:
        vm.wait_for_vm_running_and_ssh_up()
        install_randstream(vm)
        assert vbd is not None
        dev = f'/dev/{vbd.param_get("device")}'
        spans = partially_populate_device(vm, dev, config.volume_size)
        validate_partially_populated_device(vm, dev, spans)

    # Move the VM to another host of the pool
    vm.migrate(dest_host, dest_sr)
    wait_for(lambda: vm.all_vdis_on_sr(dest_sr), "Wait for all VDIs on destination SR")
    wait_for(lambda: vm.is_running_on_host(dest_host), "Wait for VM to be running on destination host")
    if integrity_check:
        validate_partially_populated_device(vm, dev, spans)

    # Migrate it back to the provenance SR
    vm.migrate(prov_host, prov_sr)
    wait_for(lambda: vm.all_vdis_on_sr(prov_sr), "Wait for all VDIs back on provenance SR")
    wait_for(lambda: vm.is_running_on_host(prov_host), "Wait for VM to be running on provenance host")
    if integrity_check:
        validate_partially_populated_device(vm, dev, spans)

    vm.shutdown(verify=True)

    if vdi_name is not None:
        vm.destroy_vdi_by_name(vdi_name)

def vdi_is_open(vdi: VDI) -> bool:
    sr = vdi.sr

    get_sr_ref = f"""
import sys
import XenAPI

def get_xapi_session():
    session = XenAPI.xapi_local()
    try:
        session.xenapi.login_with_password('root', '', '', 'xcp-ng-tests session')
    except Exception as e:
        raise Exception('Cannot get XAPI session: {{}}'.format(e))
    return session

session = get_xapi_session()
sr_ref = session.xenapi.SR.get_by_uuid(\"{sr.uuid}\")
print(sr_ref)
"""

    master = sr.pool.master
    return strtobool(master.call_plugin('on-slave', 'is_open', {
        'vdiUuid': vdi.uuid,
        'srRef': master.execute_script(get_sr_ref, shebang='python')
    }))


def install_randstream(vm: VM) -> None:
    BASE_URL = 'https://github.com/xcp-ng/randstream/releases/download'
    VERSION = '0.6.1'
    CHECKSUM = {
        'Linux': '2aef357cfdfed09d6492cfb60ab145f304abd7ccda9b77d47e15a9048c1a6eee',
        'FreeBSD': '645c007393be939d75d95388b1d0e41e0e1217e8a88057284916b638a61c690d',
    }
    TARGET_TRIPLE = {
        'Linux': 'x86_64-unknown-linux-musl',
        'FreeBSD': 'x86_64-unknown-freebsd',
    }
    version = vm.ssh('randstream --version', check=False)
    if f'randstream {VERSION}' == version:
        logging.debug("randstream is already installed")
        return
    logging.debug("Installing randstream")
    if vm.is_windows:
        raise ValueError("Windows is not currently supported")
    else:
        os_name = vm.ssh('uname -s')
        assert os_name in CHECKSUM, f"{os_name} is not currently supported"
        tt = TARGET_TRIPLE[os_name]
        cs = CHECKSUM[os_name]
        fn = '/tmp/randstream.tgz'
        vm.ssh(f"echo '{cs}  -' > {fn}.sum && wget -nv {BASE_URL}/{VERSION}/randstream-{VERSION}-{tt}.tar.gz -O - | tee {fn} | sha256sum -c {fn}.sum && tar -xzf {fn} -C /usr/bin/ ./randstream")  # noqa: E501
        vm.ssh(f"rm -f {fn} {fn}.sum")

def randstream(vm: VM, args: str) -> str:
    """
    Run randstream on the VM and return the checksum.

    The args string should contain the command and arguments to pass to randstream,
    e.g. "generate /dev/xvdb".
    """
    output = vm.ssh(f'randstream -v {args}')
    for line in output.splitlines():
        if line.startswith('checksum: '):
            return line.split(": ")[1].strip()
    raise Exception(f"Could not find the checksum in the randstream output:\n{output}")

CoalesceOperation = Literal['snapshot', 'clone']

def coalesce_integrity(vm: VM, vdi: VDI, vdi_op: CoalesceOperation, defer: Defer) -> None:
    vdi_size = vdi.get_virtual_size()
    vbd = vm.connect_vdi(vdi)
    defer(lambda: vm.disconnect_vdi(vdi))

    dev = f'/dev/{vbd.param_get("device")}'
    # generate at the start, in the middle and the end of the disk
    spans = partially_populate_device(vm, dev, vdi_size, 4, skip_spans=[1])
    # make sure we can read that exact data before the snapshot/clone
    validate_partially_populated_device(vm, dev, spans)
    new_vdi: VDI | None = None
    match vdi_op:
        case 'clone': new_vdi = vdi.clone()
        case 'snapshot': new_vdi = vdi.snapshot()
    defer(lambda: new_vdi.destroy() if new_vdi is not None else None)
    assert vdi is not None

    # add some data in a non-used place (span 1), and overwrite an already used one (span 2)
    spans[1].generate(vm, dev, seed=1)
    spans[2].generate(vm, dev, seed=2)

    # make sure we can validate that data before the coalesce
    spans[1].validate(vm, dev)
    spans[2].validate(vm, dev)

    # trigger the coalesce
    vdi.wait_for_coalesce(new_vdi.destroy)
    new_vdi = None

    # verify the data is still as expected
    validate_partially_populated_device(vm, dev, spans)

XVACompression = Literal['none', 'gzip', 'zstd']

def xva_export_import(source_vm: VM, compression: XVACompression, temp_large_dir: str, defer: Defer) -> None:
    # clone the vm, so we can resize the disk without affecting the vm from the fixture
    vm: VM | None = source_vm.clone()
    defer(lambda: vm.destroy() if vm is not None else None)
    assert vm is not None
    host = vm.host
    sr = vm.vdis[0].sr
    # we can't shrink a volume
    volume_size = max(vm.vdis[0].get_virtual_size(), config.volume_size)
    vm.vdis[0].resize(volume_size)
    # The resulting volume size is a multiple of the block size. Store the actual VDI size, so we can make comparisons
    # later in the test
    volume_size = vm.vdis[0].get_virtual_size()
    # The tests using this function are using specific fixtures to create the VM on the expected SR
    # In consequence, we can't use the storage_test_vm, so we have to start the VM explicitly and install randstream
    vm.start()
    vm.wait_for_vm_running_and_ssh_up()
    install_randstream(vm)

    is_alpine = vm.ssh_with_result('apk --version').returncode == 0
    if is_alpine:
        # growpart is not available in alpine 3.12
        # vm.ssh('apk add cloud-utils-growpart e2fsprogs-extra')
        vm.ssh('apk add gawk util-linux e2fsprogs-extra')
        vm.ssh('wget https://raw.githubusercontent.com/canonical/cloud-utils/main/bin/growpart -O /usr/bin/growpart')
        vm.ssh('chmod +x /usr/bin/growpart')
        # TODO: maybe use `findmnt -no SOURCE /` from util-linux to get the blockdevice mounted on /
        growpart_returncode = vm.ssh_with_result('growpart /dev/xvda 3').returncode
        assert growpart_returncode in [0, 1] # growpart returns 1 if the size is already the expected one
        vm.ssh('resize2fs /dev/xvda3')
        stream_size = min(volume_size // 2, config.write_volume_cap)
    else:
        stream_size = 500 * MiB

    checksum = randstream(vm, f'generate --size {stream_size} /root/data')
    randstream(vm, f'validate --expected-checksum {checksum} /root/data')
    vm.shutdown(verify=True)

    xva_path = f'{temp_large_dir}/{vm.uuid}.xva'
    defer(lambda: host.ssh(f'rm -f {xva_path}'))
    vm.export(xva_path, compression)
    # check that the zero blocks are not part of the result. Most of the data is from the random stream, so
    # compression has little effect. We just take into account the system size
    size_mb = int(vm.host.ssh(f'du -sm --apparent-size {xva_path}').split()[0])
    min_size = stream_size / MiB
    max_size = (stream_size + volume_size / 1000) * 1.1 / MiB + 200
    assert min_size < size_mb < max_size, (
        f"unexpected xva size {size_mb}MiB, was expected to be between {min_size}MiB and {max_size}MiB"
    )

    # destroy the source vm to free some space to re-import the image
    vm.destroy()
    vm = None

    imported_vm = host.import_vm(xva_path, sr.uuid)
    defer(lambda: imported_vm.destroy())
    assert imported_vm.vdis[0].get_virtual_size() == volume_size

    imported_vm.start()
    imported_vm.wait_for_vm_running_and_ssh_up()
    randstream(imported_vm, f'validate --expected-checksum {checksum} /root/data')

def vdi_export_import(vm: VM, sr: SR, image_format: ImageFormat, temp_large_dir: str, defer: Defer) -> None:
    vdi_src: VDI | None = sr.create_vdi(image_format=image_format, virtual_size=config.volume_size)
    defer(lambda: vdi_src.destroy() if vdi_src is not None else None)
    assert vdi_src is not None

    vbd = vm.connect_vdi(vdi_src)
    defer(lambda: vm.disconnect_vdi(vdi_src) if vdi_src is not None and vdi_src.uuid in vm.vdis else None)
    dev = f'/dev/{vbd.param_get("device")}'

    spans = partially_populate_device(vm, dev, config.volume_size)
    validate_partially_populated_device(vm, dev, spans)
    vm.disconnect_vdi(vdi_src)

    image_path = f'{temp_large_dir}/{vdi_src.uuid}.{image_format}'
    defer(lambda: vm.host.ssh(f'rm -f {image_path}'))

    vm.host.xe('vdi-export', {'uuid': vdi_src.uuid, 'filename': image_path, 'format': image_format})
    vdi_src.destroy()
    vdi_src = None

    # check that the zero blocks are not part of the result
    size_mb = int(vm.host.ssh(f'du -sm --apparent-size {image_path}').split()[0])
    total_span_size_mib = sum(span.size for span in spans) // MiB
    assert total_span_size_mib < size_mb < total_span_size_mib * 1.1, f"unexpected image size: {size_mb}"
    vdi_dest = sr.create_vdi(image_format=image_format, virtual_size=config.volume_size)
    defer(lambda: vdi_dest.destroy())

    vm.host.xe('vdi-import', {'uuid': vdi_dest.uuid, 'filename': image_path, 'format': image_format})
    vbd = vm.connect_vdi(vdi_dest)
    defer(lambda: vm.disconnect_vdi(vdi_dest))
    dev = f'/dev/{vbd.param_get("device")}'

    validate_partially_populated_device(vm, dev, spans)

def full_vdi_write(vm: VM, vdi: VDI, defer: Defer):
    vdi.get_virtual_size()
    vbd = vm.connect_vdi(vdi)
    defer(lambda: vm.disconnect_vdi(vdi))

    dev = f'/dev/{vbd.param_get("device")}'
    install_randstream(vm)

    checksum = randstream(vm, f'generate {dev}')
    randstream(vm, f'validate --expected-checksum {checksum} {dev}')

@dataclass
class StreamSpan:
    position: int
    size: int
    checksum: str | None = None

    def generate(self, vm: VM, dev: str, seed: int | None = None) -> str:
        """
        Generate random data for this span and return its checksum.

        Args:
            vm: Virtual machine to run randstream on
            dev: Device path (e.g., '/dev/xvdb')
            seed: Optional seed for deterministic generation. If not provided,
                  caller must ensure seed is passed explicitly.

        Returns:
            Checksum of generated data as string
        """
        seed_str = f'--seed {seed}' if seed is not None else ''
        self.checksum = randstream(
            vm, f'generate {seed_str} --position {self.position} --size {self.size} {dev}'.strip()
        )
        return self.checksum

    def validate(self, vm: VM, dev: str) -> None:
        """
        Validate random data for this span.

        Args:
            vm: Virtual machine to run randstream on
            dev: Device path (e.g., '/dev/xvdb')

        If checksum is set, validates against the expected checksum.
        Otherwise, the stream itself contains checksums for each chunk
        and will be validated using those internal checksums.
        """
        expected_flags = f'--expected-checksum {self.checksum}' if self.checksum is not None else ''
        randstream(vm, f'validate {expected_flags} --position {self.position} --size {self.size} {dev}')

def partially_populate_device(vm: VM, dev_path: str, dev_size: int, num_spans: int = 3, skip_spans: list[int] = []) \
        -> list[StreamSpan]:
    """
    Generate random data in multiple spans across a device.

    Creates num_spans spans of random data distributed across the device.
    Spans are positioned such that first span starts at 0 and last span
    ends at dev_size, with middle spans evenly distributed in between.

    ASCII visualization of span distribution:

    For num_spans=3:
        Device:  [======================================]
        Span 0:  [****]
        Span 1:                  [****]
        Span 2:                                    [****]
        Gap:     gap1            gap2                gap3

    For num_spans=4 with skip_spans=[1]:
        Device:  [================================================]
        Span 0:  [**]
        Span 1:               [  ]
        Span 2:                              [**]
        Span 3:                                                [**]

    Args:
        vm: Virtual machine to run randstream on
        dev_path: Device path (e.g., '/dev/xvdb')
        dev_size: Total device size in bytes
        num_spans: Number of spans to create (default: 3)
        skip_spans: List of span indices to skip (no data generated).
                   Skipped spans still exist in returned list with checksum=None.
                   (default: [])

    Returns:
        List of StreamSpan objects representing the generated spans.
        Spans are guaranteed to:
        - Not overlap
        - Have first span at position 0
        - Have last span ending at dev_size
        - Be evenly distributed across device
    """
    logging.info(f"Generate {dev_path} content")
    stream_size = min(dev_size, config.write_volume_cap) // num_spans

    # Validate skip_spans
    assert all(0 <= i < num_spans for i in skip_spans), \
        f"Invalid span index in skip_spans: must be 0 <= i < {num_spans}"

    spans: list[StreamSpan] = []

    # Calculate positions for regularly distributed spans
    # First span always at position 0, last span always ends at dev_size
    if num_spans == 1:
        positions = [0]
    elif num_spans == 2:
        positions = [0, dev_size - stream_size]
    else:
        # For 3+ spans: distribute evenly across available space
        # Available space is dev_size - size (last span can't extend past dev_size)
        available_space = dev_size - stream_size
        positions = [0]  # First span always at 0
        # Distribute middle spans evenly
        for i in range(1, num_spans - 1):
            position = (available_space * i) // (num_spans - 1)
            positions.append(position)
        positions.append(dev_size - stream_size)  # Last span always ends at dev_size

    # Generate spans with consistency checks
    prev_end = -1
    for i, position in enumerate(positions):
        # Assert no overlap
        assert position >= prev_end, f"Span {i} at position {position} overlaps with previous span ending at {prev_end}"
        span = StreamSpan(position=position, size=stream_size)
        if i not in skip_spans:
            span.generate(vm, dev_path, seed=1000 + i)
        spans.append(span)
        prev_end = position + stream_size

    # Final assert: last span must not extend past dev_size
    assert spans[-1].position + spans[-1].size <= dev_size, \
        f"Last span extends past device: position={spans[-1].position}, size={spans[-1].size}, dev_size={dev_size}"

    return spans

def validate_partially_populated_device(vm: VM, dev: str, spans: list[StreamSpan]) -> None:
    logging.info(f"Validate {dev} content")
    for span in spans:
        if span.checksum is not None:
            span.validate(vm, dev)
