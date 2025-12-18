import base64
import contextlib
import io
import logging
import os
import tarfile

import XenAPI

import typing

# XAPI utility functions


@contextlib.contextmanager
def xapi_session(uname="root", pwd=""):
    session = XenAPI.xapi_local()
    session.xenapi.login_with_password(uname, pwd, XenAPI.API_VERSION_1_2, "fix-efivars.py")
    try:
        yield session
    finally:
        session.xenapi.logout()


def get_pool_ref(session, pool_uuid: typing.Optional[str]):
    if pool_uuid:
        pool_ref = session.xenapi.pool.get_by_uuid(pool_uuid)
    else:
        pools = session.xenapi.pool.get_all()
        if len(pools) != 1:
            raise ValueError("Cannot automatically detect pool, please specify pool UUID")
        pool_ref = pools[0]
    if not pool_ref:
        raise ValueError("Cannot read pool ref")

    return pool_ref


def get_pool_certs_type(session, pool_ref: typing.Any, custom=True):
    certs: typing.Dict[str, bytes] = {}
    if custom:
        cert_pkg = session.xenapi.pool.get_custom_uefi_certificates(pool_ref)
        if not cert_pkg:
            return None
    else:
        cert_pkg = session.xenapi.pool.get_uefi_certificates(pool_ref)
        if not cert_pkg:
            raise RuntimeError("Cannot read default pool certs")
    logging.debug("cert_pkg len %d", len(cert_pkg))
    cert_buf = io.BytesIO(base64.b64decode(cert_pkg))

    with tarfile.open(fileobj=cert_buf) as cert_file:
        for member in cert_file:
            logging.debug("    %s %d %s", member.name, member.size, oct(member.mode))
            varname = os.path.basename(member.name)
            if varname not in ["PK.auth", "KEK.auth", "db.auth", "dbx.auth"] or not member.isfile():
                raise ValueError(f"Cannot accept certs member '{varname}'")
            data = cert_file.extractfile(member)
            if data is None:
                raise ValueError(f"Cannot read certs member '{varname}'")
            certs[os.path.splitext(varname)[0]] = data.read()

    return certs


def get_pool_certs(session, pool_ref: typing.Any):
    certs = get_pool_certs_type(session, pool_ref, custom=True)
    if certs:
        return certs, True

    certs = get_pool_certs_type(session, pool_ref, custom=False)
    if certs:
        return certs, False

    raise RuntimeError("Cannot get pool certs")
