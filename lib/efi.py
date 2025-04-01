#!/usr/bin/env python

from __future__ import print_function

import atexit
import copy
import hashlib
import logging
import os
import shutil
import struct
from datetime import datetime, timedelta
from tempfile import TemporaryDirectory, mkstemp
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import Encoding, pkcs7
from cryptography.hazmat.primitives.serialization.pkcs7 import PKCS7PrivateKeyTypes

import lib.commands as commands

from typing import Iterable, Literal, Optional, Self, Union, cast

class _EfiGlobalTempdir:
    _instance = None

    def _safe_cleanup(self):
        if self._instance is not None:
            try:
                self._instance.cleanup()
            except OSError:
                pass

    def get(self):
        if self._instance is None:
            self._instance = TemporaryDirectory()
            atexit.register(self._safe_cleanup)
        return self._instance

    def getfile(self, suffix=None, prefix=None):
        fd, path = mkstemp(suffix=suffix, prefix=prefix, dir=self.get().name)
        os.close(fd)
        return path


_tempdir = _EfiGlobalTempdir()


class GUID(UUID):
    def as_bytes(self):
        return self.bytes_le

    def as_str(self):
        return str(self)


EFI_HEADER_MAGIC = 'MZ'

global_variable_guid = GUID('8be4df61-93ca-11d2-aa0d-00e098032b8c')
image_security_database_guid = GUID('d719b2cb-3d3a-4596-a3bc-dad00e67656f')

# Variable attributes for time based authentication attrs
EFI_AT_ATTRS = 0x27

time_seed = datetime.now()
time_offset = 1

p7_out = ''

WIN_CERT_TYPE_EFI_GUID = 0x0EF1

u8 = 'B'
u16 = 'H'
u32 = 'I'
EFI_GUID = '16s'


def efi_pack(*args):
    """
    Return bytes of an EFI struct (little endian).

    EFI structs are all packed as little endian.
    """
    return struct.pack('<' + args[0], *args[1:])


def pack_guid(data1, data2, data3, data4):
    return b''.join([
        struct.pack(u32, data1),
        struct.pack(u16, data2),
        struct.pack(u16, data3),
        bytes(data4),
    ])


EFI_AT_ATTRS_BYTES = efi_pack(u32, EFI_AT_ATTRS)

EFI_CERT_PKCS7_GUID = pack_guid(
    0x4AAFD29D,
    0x68DF,
    0x49EE,
    [0x8A, 0xA9, 0x34, 0x7D, 0x37, 0x56, 0x65, 0xA7],
)

VATES_GUID = pack_guid(0xFDD69FA4, 0x3E66, 0x11EB, [0x8C, 0x1B, 0x98, 0x3B, 0x8F, 0xB6, 0xDA, 0xCD])

EFI_CERT_X509_GUID = pack_guid(0xA5C059A1, 0x94E4, 0x4AA7, [0x87, 0xB5, 0xAB, 0x15, 0x5C, 0x2B, 0xF0, 0x72])

WIN_CERTIFICATE = ''.join([u32, u16, u16])
WIN_CERTIFICATE_UEFI_GUID = ''.join([WIN_CERTIFICATE, EFI_GUID])
WIN_CERTIFICATE_UEFI_GUID_offset = struct.calcsize(WIN_CERTIFICATE_UEFI_GUID)

EFI_TIME = ''.join([
    u16,  # Year
    u8,  # Month
    u8,  # Day
    u8,  # Hour
    u8,  # Minute
    u8,  # Second
    u8,  # Pad1
    u32,  # Nanosecond
    u16,  # TimeZone
    u8,  # Daylight
    u8,  # Pad2
])

EFI_VARIABLE_AUTHENTICATION_2 = ''.join([EFI_TIME, WIN_CERTIFICATE_UEFI_GUID])
EFI_SIGNATURE_DATA = EFI_GUID
EFI_SIGNATURE_DATA_size = struct.calcsize(EFI_SIGNATURE_DATA)
EFI_SIGNATURE_LIST = ''.join([EFI_GUID, u32, u32, u32])
EFI_SIGNATURE_LIST_size = struct.calcsize(EFI_SIGNATURE_LIST)
EFI_SIGNATURE_DATA_offset = 16

SECURE_BOOT_VARIABLES = {"PK", "KEK", "db", "dbx"}


def get_secure_boot_guid(variable: str) -> GUID:
    """Return the GUID for an EFI secure boot variable."""
    return {
        'PK': global_variable_guid,
        'KEK': global_variable_guid,
        'db': image_security_database_guid,
        'dbx': image_security_database_guid,
    }[variable]


def cert_to_efi_sig_list(cert):
    """Return an ESL from a PEM cert."""
    with open(cert, 'rb') as f:
        cert_raw = f.read()
        # Cert files can come in either PEM or DER form, and we can't assume
        # that they come in a specific form. Since `cryptography` doesn't have
        # a way to detect cert format, we have to detect it ourselves.
        try:
            cert = x509.load_pem_x509_certificate(cert_raw)
        except ValueError:
            cert = x509.load_der_x509_certificate(cert_raw)
        der = cert.public_bytes(Encoding.DER)

    signature_type = EFI_CERT_X509_GUID
    signature_list_size = len(der) + EFI_SIGNATURE_LIST_size + EFI_SIGNATURE_DATA_size
    signature_header_size = 0
    signature_size = signature_list_size - EFI_SIGNATURE_LIST_size
    signature_owner = VATES_GUID

    return (
        efi_pack(
            EFI_SIGNATURE_LIST,
            bytes(signature_type),
            signature_list_size,
            signature_header_size,
            signature_size,
        )
        + efi_pack(EFI_SIGNATURE_DATA, bytes(signature_owner))
        + der
    )


def certs_to_sig_db(certs) -> bytes:
    """Returns a signature database from a list cert file paths."""
    if isinstance(certs, str):
        certs = [certs]

    db = b''

    for i, cert in enumerate(certs):
        tmp = cert_to_efi_sig_list(cert)
        logging.debug('Size of Cert %d: %d' % (i, len(tmp)))
        db += tmp

    return db


def sign_efi_sig_db(
    sig_db: bytes, var: str, key: str, cert: str, time: Optional[datetime] = None, guid: Optional[GUID] = None
):
    """Return a pkcs7 SignedData from a UEFI signature database."""
    global p7_out

    if guid is None:
        guid = get_secure_boot_guid(var)

    if time is None:
        time = datetime.now()

    timestamp = efi_pack(
        EFI_TIME,
        time.year,
        time.month,
        time.day,
        time.hour,
        time.minute,
        time.second,
        0,
        0,
        0,
        0,
        0,
    )

    logging.debug(
        'Timestamp is %d-%d-%d %02d:%02d:%02d' % (time.year, time.month, time.day, time.hour, time.minute, time.second)
    )

    var_utf16 = var.encode('utf-16-le')
    attributes = EFI_AT_ATTRS_BYTES

    # From UEFI spec (2.6):
    #    digest = hash (VariableName, VendorGuid, Attributes, TimeStamp,
    #                   DataNew_variable_content)
    payload = var_utf16 + guid.as_bytes() + attributes + timestamp + sig_db

    logging.debug('Signature DB Size: %d' % len(sig_db))
    logging.debug('Authentication Payload size %d' % len(payload))

    p7 = sign(payload, key, cert)

    if p7_out:
        with open(p7_out, 'wb') as f:
            f.write(p7)

    return create_auth2_header(p7, timestamp) + p7 + sig_db


def sign(payload: bytes, key_file: str, cert_file: str):
    """Returns a signed PKCS7 of payload signed by key and cert."""
    with open(key_file, 'rb') as f:
        priv_key = cast(PKCS7PrivateKeyTypes, serialization.load_pem_private_key(f.read(), password=None))

    with open(cert_file, 'rb') as f:
        cert = x509.load_pem_x509_certificate(f.read())

    options = [
        pkcs7.PKCS7Options.DetachedSignature,
        pkcs7.PKCS7Options.Binary,
        pkcs7.PKCS7Options.NoAttributes,
    ]

    return (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(payload)
        .add_signer(cert, priv_key, hashes.SHA256())
        .sign(serialization.Encoding.DER, options)
    )


def create_auth2_header(sig_db: bytes, timestamp: bytes):
    """Return an EFI_AUTHENTICATE_VARIABLE_2 from a signature database."""
    length = len(sig_db) + WIN_CERTIFICATE_UEFI_GUID_offset
    revision = 0x200
    win_cert = efi_pack(WIN_CERTIFICATE, length, revision, WIN_CERT_TYPE_EFI_GUID)
    auth_info = win_cert + EFI_CERT_PKCS7_GUID

    return timestamp + auth_info


def timestamp():
    global time_offset
    time_offset += 1
    return time_seed + timedelta(seconds=time_offset)


def get_signed_name(image: str):
    fpath, ext = os.path.splitext(image)
    return fpath + '-signed' + ext


def pesign(key, cert, name, image):
    """Sign a binary using pesign."""
    with TemporaryDirectory(prefix='certdir_') as certdir:
        # Setup pesign cert dir. commands taken from:
        #     https://en.opensuse.org/openSUSE:UEFI_Image_File_Sign_Tools
        common_name = name + ' Owner'
        commands.local_cmd(['certutil', '-N', '-d', certdir, '--empty-password'])
        commands.local_cmd(['certutil', '-A', '-n', common_name, '-d', certdir, '-t', 'CT,CT,CT', '-i', cert])

        pk12 = os.path.join(certdir, '%s.p12' % name)

        # Create a pk12 out of the cert and key
        password = 'root'
        commands.local_cmd([
            'openssl',
            'pkcs12',
            '-export',
            '-out',
            pk12,
            '-in',
            cert,
            '-inkey',
            key,
            '-passin',
            'pass:' + password,
            '-passout',
            'pass:' + password,
        ])

        # Enroll the pk12 to the cert database for pesign to use
        commands.local_cmd(['pk12util', '-d', certdir, '-i', pk12, '-W', password])
        signed = get_signed_name(image)

        # Sign the image
        commands.local_cmd(['pesign', '-f', '-n', certdir, '-c', common_name, '-s', '-i', image, '-o', signed])

        return signed


class Certificate:
    def __init__(self, pub: str, key: Optional[str]):
        self.pub = pub
        self.key = key

    @classmethod
    def self_signed(cls, common_name='XCP-ng Test Common Name'):
        pub = _tempdir.getfile(suffix='.pem')
        key = _tempdir.getfile(suffix='.pem')

        commands.local_cmd([
            'openssl', 'req', '-new', '-x509', '-newkey', 'rsa:2048',
            '-subj', '/CN=%s/' % common_name, '-nodes', '-keyout',
            key, '-sha256', '-days', '3650', '-out', pub
        ])

        return cls(pub, key)

    def sign_efi_sig_db(self, var: str, data: bytes, guid: Optional[GUID]):
        assert self.key is not None
        return sign_efi_sig_db(data, var, self.key, self.pub, time=timestamp(), guid=guid)

    def copy(self):
        newpub = _tempdir.getfile(suffix='.pem')
        shutil.copyfile(self.pub, newpub)

        newkey = None
        if self.key is not None:
            newkey = _tempdir.getfile(suffix='.pem')
            shutil.copyfile(self.key, newkey)

        return Certificate(newpub, newkey)


class EFIAuth:
    _auth_data: Optional[bytes]
    name: Literal["PK", "KEK", "db", "dbx"]

    def __init__(
        self,
        name: Literal["PK", "KEK", "db", "dbx"],
        owner_cert: Optional[Certificate] = None,
        other_certs: Optional[Iterable[Union[Certificate, str]]] = None,
    ):
        assert name in SECURE_BOOT_VARIABLES
        # No point having an owner cert without a matching private key
        assert owner_cert is None or owner_cert.key is not None
        self.name = name
        self.guid = get_secure_boot_guid(self.name)
        self._owner_cert = owner_cert
        self._other_certs = list(other_certs or [])
        self._efi_signature_list = self._get_efi_signature_list()
        self._auth_data = None
        self._auth = _tempdir.getfile(suffix='.auth')

    @classmethod
    def self_signed(
        cls, name: Literal["PK", "KEK", "db", "dbx"], other_certs: Optional[Iterable[Union[Certificate, str]]] = None
    ):
        return cls(name, owner_cert=Certificate.self_signed(name + " Owner"), other_certs=other_certs)

    def owner_cert(self):
        assert self._owner_cert is not None
        return self._owner_cert

    def is_signed(self):
        return self._auth_data is not None

    def auth_data(self):
        assert self.is_signed()
        return self._auth_data

    def auth(self):
        assert self.is_signed()
        return self._auth

    def sign_with(self, signer: Self):
        """Sign this object, using another EFIAuth object as signer."""
        owner_cert = signer.owner_cert()
        assert owner_cert is not None

        self._auth_data = owner_cert.sign_efi_sig_db(self.name, self._efi_signature_list, self.guid)

        with open(self._auth, 'wb') as f:
            f.write(self._auth_data)

    def sign_auth(self, to_be_signed: Self):
        """
        Sign another EFIAuth object.

        The other EFIAuth's member `auth` will be set to
        the path of the .auth file.
        """
        to_be_signed.sign_with(self)

    def sign_image(self, image: str) -> str:
        """
        Sign an EFI image.

        The arg `image` is the path to an EFI image (such as grubx64.efi).

        The EFI image can be any PE/COFF binary.  The UEFI spec calls them images,
        but 'binary' probably better fits community terminology.

        Returns path to signed image.
        """
        assert self._owner_cert is not None
        if shutil.which('sbsign'):
            signed = get_signed_name(image)
            commands.local_cmd([
                'sbsign', '--key', self._owner_cert.key, '--cert', self._owner_cert.pub,
                image, '--output', signed
            ])
        else:
            signed = pesign(self._owner_cert.key, self._owner_cert.pub, self.name, image)

        return signed

    def copy(self, name: Optional[Literal["PK", "KEK", "db", "dbx"]] = None):
        """
        Make a copy of an existing EFIAuth object.

        Specify a name to copy the certs, but to change the filenames
        to retain a new name.

        Note: the backing .auth files will not be regenerated with the new
        name, so attempting to set a copied variable at runtime, not in custom
        mode, will fail due a mismatch in the hash (containing the old name)
        and the new data (containing the new name).  This doesn't affect
        tests right now because the certs are set from the dom0, which bypasses
        guest runtime checks.

        TODO: copy other's cert and ask the signer to call sign_auth() on
              the new obj.  This will recalculate the digest and the new
              signature will be correct, and will pass guest runtime checks.

        This is ONLY useful for creating a new handle.
        """
        assert self._owner_cert is not None

        newname = name or self.name

        copied = EFIAuth(name=newname, owner_cert=self._owner_cert.copy(), other_certs=self._other_certs.copy())
        copied._efi_signature_list = self._efi_signature_list

        if self.is_signed():
            copied._auth_data = copy.copy(self._auth_data)
            shutil.copyfile(self._auth, copied._auth)

        return copied

    def _get_efi_signature_list(self) -> bytes:
        certs = []
        if self._owner_cert is not None:
            certs.append(self._owner_cert.pub)
        for other_cert in self._other_certs:
            if isinstance(other_cert, str):
                certs.append(other_cert)
            elif isinstance(other_cert, Certificate):
                certs.append(other_cert.pub)
            else:
                raise TypeError('other_cert is not Certificate or str')

        return certs_to_sig_db(certs)


def esl_from_auth_file(auth: str) -> bytes:
    """
    Return the ESL contained inside the EFI auth file.

    Warning: This will break if used on any auth file containing an ESL of
             certs of non-X509 GUID type. All of the certs used in Secure Boot are X509
             GUID type.
    """
    data = b""
    with open(auth, "rb") as f:
        data = f.read()
    return esl_from_auth_bytes(data)


def esl_from_auth_bytes(auth_data: bytes) -> bytes:
    """
    Return the ESL contained inside the AUTH2 structure.

    Warning: This will break if used on any ESL containing certs of non-X509 GUID type.
             All of the certs used in Secure Boot are X509 GUID type.
    """
    return auth_data[auth_data.index(EFI_CERT_X509_GUID):]


def get_md5sum_from_auth(auth: str):
    return hashlib.md5(esl_from_auth_file(auth)).hexdigest()


if __name__ == '__main__':
    import argparse
    import sys

    epilog = '''
Examples:

    # Create a PK.auth (self-signed)
    {prog} -k PK.key -c PK.cert PK PK.auth PK.cert

    # Create a KEK.auth
    {prog} -k PK.key -c PK.cert KEK KEK.auth KEK.cert

    # Create a db.auth
    {prog} -k KEK.key -c KEK.cert db db.auth db.cert
'''.format(prog=sys.argv[0])

    parser = argparse.ArgumentParser(
        description='Create signed UEFI secure boot variables',
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('-k', '--key', type=str, help='The signers key')
    parser.add_argument('-c', '--cert', type=str, help='The signers cert (PEM)')
    parser.add_argument('--log', type=str, choices=['DEBUG', 'INFO'])
    parser.add_argument(
        '--p7',
        type=str,
        help='Output the intermediary p7 data object (useful for debug)',
    )
    parser.add_argument(
        'var',
        type=str,
        choices=['PK', 'KEK', 'db', 'dbx'],
        help='The variable name for the cert',
    )
    parser.add_argument('outputfile', type=str, help='The name of the output file')
    parser.add_argument('certs', nargs='+', help='The new certs for the variable')
    args = parser.parse_args()

    if args.log:
        logging.basicConfig(format='%(levelname)s:%(message)s', level=getattr(logging, args.log.upper()))

    if args.p7:
        p7_out = args.p7

    db = certs_to_sig_db(args.certs)
    auth = sign_efi_sig_db(db, args.var, args.key, args.cert)

    with open(args.outputfile, 'wb') as f:
        f.write(auth)
