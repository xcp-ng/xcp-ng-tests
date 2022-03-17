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
