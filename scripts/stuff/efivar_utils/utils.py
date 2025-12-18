import contextlib
import logging
import os
import struct
import tempfile

import typing

def unserialize(format: typing.Union[str, bytes], buf: bytes, offset: int = 0):
    return (buf[struct.calcsize(format) :],) + struct.unpack_from(format, buf, offset)


def unserialize_struct(s: struct.Struct, buf: bytes, offset: int = 0):
    return (buf[s.size :],) + s.unpack_from(buf, offset)


def unserialize_data(buf: bytes, rem: int, limit: int):
    buf, buflen = unserialize("<Q", buf)
    logging.debug("    nextlen %d", buflen)

    if buflen > rem:
        raise ValueError("nextlen > rem")
    if buflen == 0:
        raise ValueError("nextlen == 0")
    if buflen > limit:
        logging.debug("    ! nextlen > limit")

    var = buf[:buflen]
    buf = buf[buflen:]

    return buf, var


@contextlib.contextmanager
def named_temporary_file(mode="w+b", suffix=None, prefix=None, dir=None, delete=True, **kwargs):
    """
    Unlike tempfile.NamedTemporaryFile, this function only deletes the temp file at context manager exit.
    Also unlike tempfile.NamedTemporaryFile, it returns a tuple (fileobj, path) rather than passing the path in
    fileobj.name.
    """
    # delete_on_close doesn't exist on old Python versions, so we have to use this roundabout method
    tmp_name = None
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=dir, text="b" not in mode)
        with os.fdopen(tmp_fd, mode, **kwargs) as tmp:
            yield tmp, tmp_name
    finally:
        if delete and tmp_name:
            os.unlink(tmp_name)
