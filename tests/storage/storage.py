from __future__ import annotations

import pytest

import logging

from lib import config
from lib.commands import SSHCommandFailed
from lib.common import Defer, GiB, KiB, MiB, TiB, strtobool, wait_for, wait_for_not
from lib.host import Host
from lib.sr import SR
from lib.vdi import VDI, ImageFormat
from lib.vm import VM

from typing import Literal, Tuple

MAX_VDI_SIZE: dict[ImageFormat, int] = {'qcow2': 16 * TiB, 'vhd': 2040 * GiB}

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

def partial_stream_size(vdi_size: int) -> int:
    return min((vdi_size // 16 // (32 * KiB)) * (32 * KiB), 2 * GiB)

def partially_populate_device(vm: VM, dev_path: str, dev_size: int) -> Tuple[str, str, str]:
    logging.info(f"Generate {dev_path} content")
    size = partial_stream_size(dev_size)
    # generate at the start, in the middle and the end of the disk
    # use seeds unlikely to collide with other usages
    checksum1 = randstream(vm, f'generate --seed 10 --size {size} {dev_path}')
    checksum2 = randstream(vm, f'generate --seed 11 --position {dev_size // 2} --size {size} {dev_path}')
    checksum3 = randstream(vm, f'generate --seed 12 --position {dev_size - size} --size {size} {dev_path}')
    return (checksum1, checksum2, checksum3)

def validate_partially_populated_device(vm: VM, dev_path: str, dev_size: int, checksums: Tuple[str, str, str]) -> None:
    logging.info(f"Validate {dev_path} content")
    size = partial_stream_size(dev_size)
    checksum1, checksum2, checksum3 = checksums
    randstream(vm, f'validate --expected-checksum {checksum1} --size {size} {dev_path}')
    randstream(vm, f'validate --expected-checksum {checksum2} --position {dev_size // 2} --size {size} {dev_path}')
    randstream(vm, f'validate --expected-checksum {checksum3} --position {dev_size - size} --size {size} {dev_path}')


def cold_migration_then_come_back(vm: VM, prov_host: Host, dest_host: Host, dest_sr: SR) -> None:
    """ Storage migration of a shutdown VM, then migrate it back. """
    prov_sr = vm.get_sr()
    vdi_name: str | None = None
    integrity_check = not vm.is_windows
    dev = ""
    checksums = ('', '', '')

    if integrity_check:
        # the vdi will be destroyed with the vm
        vdi = prov_sr.create_vdi(virtual_size=config.volume_size)
        vdi_name = vdi.name()
        vbd = vm.connect_vdi(vdi)
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()
        install_randstream(vm)
        dev = f'/dev/{vbd.param_get("device")}'
        checksums = partially_populate_device(vm, dev, config.volume_size)
        validate_partially_populated_device(vm, dev, config.volume_size, checksums)
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
        validate_partially_populated_device(vm, dev, config.volume_size, checksums)
    vm.shutdown(verify=True)

    # Migrate it back to the provenance SR
    vm.migrate(prov_host, prov_sr)
    wait_for(lambda: vm.all_vdis_on_sr(prov_sr), "Wait for all VDIs back on provenance SR")

    # Start VM to make sure it works
    vm.start(on=prov_host.uuid)
    vm.wait_for_os_booted()
    if integrity_check:
        vm.wait_for_vm_running_and_ssh_up()
        validate_partially_populated_device(vm, dev, config.volume_size, checksums)
    vm.shutdown(verify=True)

    if vdi_name is not None:
        vm.destroy_vdi_by_name(vdi_name)

def live_storage_migration_then_come_back(vm: VM, prov_host: Host, dest_host: Host, dest_sr: SR) -> None:
    prov_sr = vm.get_sr()
    vdi_name: str | None = None
    integrity_check = not vm.is_windows
    dev = ""
    checksums = ('', '', '')
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
        checksums = partially_populate_device(vm, dev, config.volume_size)
        validate_partially_populated_device(vm, dev, config.volume_size, checksums)

    # Move the VM to another host of the pool
    vm.migrate(dest_host, dest_sr)
    wait_for(lambda: vm.all_vdis_on_sr(dest_sr), "Wait for all VDIs on destination SR")
    wait_for(lambda: vm.is_running_on_host(dest_host), "Wait for VM to be running on destination host")
    if integrity_check:
        validate_partially_populated_device(vm, dev, config.volume_size, checksums)

    # Migrate it back to the provenance SR
    vm.migrate(prov_host, prov_sr)
    wait_for(lambda: vm.all_vdis_on_sr(prov_sr), "Wait for all VDIs back on provenance SR")
    wait_for(lambda: vm.is_running_on_host(prov_host), "Wait for VM to be running on provenance host")
    if integrity_check:
        validate_partially_populated_device(vm, dev, config.volume_size, checksums)

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
    VERSION = '0.5.0'
    CHECKSUM = {
        'Linux': '31ece6ea8f605aa3046609b37c72bdc11b39ee5942e8a0a8e2a052c50df00026',
        'FreeBSD': '12fcaad99d892963af84f2a3861a7b38a14e96cc6b3a3e45d78fb76a69b421f5',
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
    stream_size = partial_stream_size(vdi_size)
    vbd = vm.connect_vdi(vdi)
    defer(lambda: vm.disconnect_vdi(vdi))

    dev = f'/dev/{vbd.param_get("device")}'
    # generate at the start, in the middle and the end of the disk
    checksum1, checksum2, checksum3 = partially_populate_device(vm, dev, vdi_size)
    # make sure we can read that exact data before the snapshot/clone
    validate_partially_populated_device(vm, dev, vdi_size, (checksum1, checksum2, checksum3))
    new_vdi: VDI | None = None
    match vdi_op:
        case 'clone': new_vdi = vdi.clone()
        case 'snapshot': new_vdi = vdi.snapshot()
    defer(lambda: new_vdi.destroy() if new_vdi is not None else None)
    assert vdi is not None

    # add some data in a non-used place, and overwrite an already used one
    checksum2bis = randstream(vm, f'generate --seed 0 --position {vdi_size // 2} --size {stream_size} {dev}')
    checksum4 = randstream(vm, f'generate --seed 1 --position {stream_size} --size {stream_size} {dev}')
    # make sure we can write that data before the coalesce
    randstream(
        vm, f'validate --expected-checksum {checksum2bis} --position {vdi_size // 2} --size {stream_size} {dev}',
    )
    randstream(
        vm, f'validate --expected-checksum {checksum4} --position {stream_size} --size {stream_size} {dev}'
    )

    # trigger the coalesce
    vdi.wait_for_coalesce(new_vdi.destroy)
    new_vdi = None

    # verify the data is still as expected
    validate_partially_populated_device(vm, dev, vdi_size, (checksum1, checksum2bis, checksum3))
    randstream(
        vm, f'validate --expected-checksum {checksum4} --position {stream_size} --size {stream_size} {dev}'
    )

XVACompression = Literal['none', 'gzip', 'zstd']

def xva_export_import(vm: VM, compression: XVACompression, temp_large_dir: str, defer: Defer) -> None:
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
        stream_size = min(volume_size // 2, 2 * GiB)
    else:
        stream_size = 500 * MiB

    checksum = randstream(vm, f'generate --size {stream_size} /root/data')
    randstream(vm, f'validate --expected-checksum {checksum} /root/data')
    vm.shutdown(verify=True)

    xva_path = f'{temp_large_dir}/{vm.uuid}.xva'
    defer(lambda: vm.host.ssh(f'rm -f {xva_path}'))
    vm.export(xva_path, compression)
    # check that the zero blocks are not part of the result. Most of the data is from the random stream, so
    # compression has little effect. We just take into account the system size
    size_mb = int(vm.host.ssh(f'du -sm --apparent-size {xva_path}').split()[0])
    min_size = stream_size / MiB
    max_size = (stream_size + volume_size / 1000) * 1.1 / MiB + 200
    assert min_size < size_mb < max_size, (
        f"unexpected xva size {size_mb}MiB, was expected to be between {min_size}MiB and {max_size}MiB"
    )

    imported_vm = vm.host.import_vm(xva_path, vm.vdis[0].sr.uuid)
    defer(lambda: imported_vm.destroy())
    assert vm.vdis[0].get_virtual_size() == volume_size

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

    checksums = partially_populate_device(vm, dev, config.volume_size)
    validate_partially_populated_device(vm, dev, config.volume_size, checksums)
    vm.disconnect_vdi(vdi_src)

    image_path = f'{temp_large_dir}/{vdi_src.uuid}.{image_format}'
    defer(lambda: vm.host.ssh(f'rm -f {image_path}'))

    vm.host.xe('vdi-export', {'uuid': vdi_src.uuid, 'filename': image_path, 'format': image_format})
    vdi_src.destroy()
    vdi_src = None

    # check that the zero blocks are not part of the result
    size_mb = int(vm.host.ssh(f'du -sm --apparent-size {image_path}').split()[0])
    stream_size = partial_stream_size(config.volume_size)
    assert stream_size // MiB * 3 < size_mb < stream_size // MiB * 3.1, f"unexpected image size: {size_mb}"
    vdi_dest = sr.create_vdi(image_format=image_format, virtual_size=config.volume_size)
    defer(lambda: vdi_dest.destroy())

    vm.host.xe('vdi-import', {'uuid': vdi_dest.uuid, 'filename': image_path, 'format': image_format})
    vbd = vm.connect_vdi(vdi_dest)
    defer(lambda: vm.disconnect_vdi(vdi_dest))
    dev = f'/dev/{vbd.param_get("device")}'

    validate_partially_populated_device(vm, dev, config.volume_size, checksums)

def full_vdi_write(vm: VM, vdi: VDI, defer: Defer):
    vdi.get_virtual_size()
    vbd = vm.connect_vdi(vdi)
    defer(lambda: vm.disconnect_vdi(vdi))

    dev = f'/dev/{vbd.param_get("device")}'
    install_randstream(vm)

    checksum = randstream(vm, f'generate {dev}')
    randstream(vm, f'validate --expected-checksum {checksum} {dev}')
