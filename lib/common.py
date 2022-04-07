import logging
import time
from enum import Enum
from uuid import UUID

class PackageManagerEnum(Enum):
    UNKNOWN = 1
    RPM = 2
    APT_GET = 3

# Common VM images used in tests
def vm_image(vm_key):
    from data import VM_IMAGES, DEF_VM_URL
    url = VM_IMAGES[vm_key]
    if not url.startswith('http'):
        url = DEF_VM_URL + url
    return url

def objects_name_prefix():
    try:
        from data import OBJECTS_NAME_PREFIX
        name_prefix = OBJECTS_NAME_PREFIX
    except ImportError:
        name_prefix = 'TEST'
    finally:
        return name_prefix

def wait_for(fn, msg=None, timeout_secs=120, retry_delay_secs=2, invert=False):
    if msg is not None:
        logging.info(msg)
    time_left = timeout_secs
    while True:
        ret = fn()
        if not invert and ret:
            return
        if invert and not ret:
            return
        time_left -= retry_delay_secs
        if time_left <= 0:
            expected = 'True' if not invert else 'False'
            raise TimeoutError(
                "Timeout reached while waiting for fn call to yield %s (%s)." % (expected, timeout_secs)
            )
        time.sleep(retry_delay_secs)

def wait_for_not(*args, **kwargs):
    return wait_for(*args, **kwargs, invert=True)

def is_uuid(maybe_uuid):
    try:
        UUID(maybe_uuid, version=4)
        return True
    except ValueError:
        return False

def to_xapi_bool(b):
    return 'true' if b else 'false'

def parse_xe_dict(xe_dict):
    """
    Parses a xe param containing keys and values, e.g. "major: 7; minor: 20; micro: 0; build: 3".

    Data type remains str for all values.
    """
    res = {}
    for pair in xe_dict.split(';'):
        key, value = pair.split(':')
        res[key.strip()] = value.strip()
    return res

def safe_split(text, sep=','):
    """ A split function that returns an empty list if the input string is empty. """
    return text.split(sep) if len(text) > 0 else []

def setup_formatted_and_mounted_disk(host, sr_disk, fs_type, mountpoint):
    if fs_type == 'ext4':
        option_force = '-F'
    elif fs_type == 'xfs':
        option_force = '-f'
    else:
        raise Exception(f"Unsupported fs_type '{fs_type}' in this function")
    device = '/dev/' + sr_disk
    logging.info(f">> Format sr_disk {sr_disk} and mount it on host {host}")
    host.ssh(['mkfs.' + fs_type, option_force, device])
    host.ssh(['rm', '-rf', mountpoint]) # Remove any existing leftover to ensure rmdir will not fail in teardown
    host.ssh(['mkdir', '-p', mountpoint])
    host.ssh(['cp', '-f', '/etc/fstab', '/etc/fstab.orig'])
    host.ssh(['echo', f'{device} {mountpoint} {fs_type} defaults 0 0', '>>/etc/fstab'])
    try:
        host.ssh(['mount', mountpoint])
    except Exception:
        # restore fstab then re-raise
        host.ssh(['cp', '-f', '/etc/fstab.orig', '/etc/fstab'])
        raise

def teardown_formatted_and_mounted_disk(host, mountpoint):
    logging.info(f"<< Restore fstab and unmount {mountpoint} on host {host}")
    host.ssh(['cp', '-f', '/etc/fstab.orig', '/etc/fstab'])
    host.ssh(['umount', mountpoint])
    host.ssh(['rmdir', mountpoint])
