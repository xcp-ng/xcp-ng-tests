import getpass
import inspect
import logging
import time
import traceback
from enum import Enum
from uuid import UUID

import lib.commands as commands

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

def prefix_object_name(label):
    try:
        from data import OBJECTS_NAME_PREFIX
        name_prefix = OBJECTS_NAME_PREFIX
    except ImportError:
        name_prefix = f"[{getpass.getuser()}]"
    return f"{name_prefix} {label}"

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

def exec_nofail(func):
    """ Execute a function, log a warning if it fails, and return eiter [] or [e] where e is the exception. """
    caller_name = inspect.stack()[1].function
    try:
        func()
        return []
    except Exception as e:
        logging.warning(
            f"An error occurred in `{caller_name}`\n"
            f"Backtrace:\n{traceback.format_exc()}"
        )
        return [e]

def raise_errors(errors):
    if not errors:
        return
    elif len(errors) == 1:
        raise errors[0]
    else:
        raise Exception("Several exceptions were catched: " + "\n".join(repr(e) for e in errors))

def strtobool(str):
    # Note: `distutils` package is deprecated and slated for removal in Python 3.12.
    # There is not alternative for strtobool.
    # See: https://peps.python.org/pep-0632/#migration-advice
    # So this is a custom implementation with differences:
    # - A boolean is returned instead of integer
    # - Empty string and None are supported (False is returned in this case)
    if not str:
        return False
    str = str.lower()
    if str in ('y', 'yes', 't', 'true', 'on', '1'):
        return True
    if str in ('n', 'no', 'f', 'false', 'off', '0'):
        return False
    raise ValueError("invalid truth value '{}'".format(str))

def _param_get(host, xe_prefix, uuid, param_name, key=None, accept_unknown_key=False):
    """ Common implementation for param_get. """
    args = {'uuid': uuid, 'param-name': param_name}
    if key is not None:
        args['param-key'] = key
    try:
        value = host.xe(f'{xe_prefix}-param-get', args)
    except commands.SSHCommandFailed as e:
        if key and accept_unknown_key and e.stdout == "Error: Key %s not found in map" % key:
            value = None
        else:
            raise
    return value

def _param_set(host, xe_prefix, uuid, param_name, value, key=None):
    """ Common implementation for param_set. """
    args = {'uuid': uuid}

    if key is not None:
        param_name = '{}:{}'.format(param_name, key)

    args[param_name] = value

    host.xe(f'{xe_prefix}-param-set', args)

def _param_add(host, xe_prefix, uuid, param_name, value, key=None):
    """ Common implementation for param_add. """
    param_key = f'{key}={value}' if key is not None else value
    args = {'uuid': uuid, 'param-name': param_name, 'param-key': param_key}

    host.xe(f'{xe_prefix}-param-add', args)

def _param_remove(host, xe_prefix, uuid, param_name, key, accept_unknown_key=False):
    """ Common implementation for param_remove. """
    args = {'uuid': uuid, 'param-name': param_name, 'param-key': key}
    try:
        host.xe(f'{xe_prefix}-param-remove', args)
    except commands.SSHCommandFailed as e:
        if not accept_unknown_key or e.stdout != "Error: Key %s not found in map" % key:
            raise

def _param_clear(host, xe_prefix, uuid, param_name):
    """ Common implementation for param_clear. """
    args = {'uuid': uuid, 'param-name': param_name}
    host.xe(f'{xe_prefix}-param-clear', args)
