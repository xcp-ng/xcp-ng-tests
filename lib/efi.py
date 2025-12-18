import atexit
import copy
import hashlib
import logging
import os
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory, mkstemp

from xcp_efivar_utils.efi import (
    EFI_CERT_X509_GUID,
    EFI_VARIABLE_SECUREBOOT_KEYS,
    SECURE_BOOT_VARIABLES,
    make_efi_signature_data_x509,
    make_efi_signature_list,
    make_efi_variable_authentication_2,
)
from xcp_efivar_utils.utils import read_certificate_as_der

import lib.commands as commands

from typing import Iterable, Literal, Optional, Self, Union

# Test library for EFI


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


class _SecureBootCertList:
    _prefix = Path(__file__).parent / '../contrib'

    def kek_ms_2011(self):
        return str(self._prefix / "secureboot_objects/KEK/Certificates/MicCorKEKCA2011_2011-06-24.der")

    def kek_ms_2023(self):
        return str(self._prefix / "secureboot_objects/KEK/Certificates/microsoft corporation kek 2k ca 2023.der")

    def db_win_2011(self):
        return str(self._prefix / "secureboot_objects/DB/Certificates/MicWinProPCA2011_2011-10-19.der")

    def db_uefi_2011(self):
        return str(self._prefix / "secureboot_objects/DB/Certificates/MicCorUEFCA2011_2011-06-27.der")

    def db_win_2023(self):
        return str(self._prefix / "secureboot_objects/DB/Certificates/windows uefi ca 2023.der")

    def db_uefi_2023(self):
        return str(self._prefix / "secureboot_objects/DB/Certificates/microsoft uefi ca 2023.der")

    def db_oprom_2023(self):
        return str(self._prefix / "secureboot_objects/DB/Certificates/microsoft option rom uefi ca 2023.der")

    def dbx_hashes_ms_amd64(self):
        return str(self._prefix / "secureboot_objects/DBX/amd64/DBXUpdate.bin")

    def dbx_poison(self):
        return str(self._prefix / "varstored/dbx_poison.auth")


SB_CERTS = _SecureBootCertList()
EFI_HEADER_MAGIC = 'MZ'
TEST_OWNER_GUID = uuid.UUID('fdd69fa4-3e66-11eb-8c1b-983b8fb6dacd')


time_seed = datetime.now()
time_offset = 1


def timestamp():
    global time_offset
    time_offset += 1
    return time_seed + timedelta(seconds=time_offset)


def certs_to_sig_db(certs) -> bytes:
    """Returns a signature database from a list cert file paths."""
    if isinstance(certs, str):
        certs = [certs]

    esls = []

    for i, cert in enumerate(certs):
        cert_bytes = read_certificate_as_der(cert, _tempdir.get().name)
        tmp = make_efi_signature_data_x509(TEST_OWNER_GUID, cert_bytes)
        logging.debug('Size of Cert %d: %d' % (i, len(tmp)))
        # Each cert must be in its own ESL, all of which are concatenated at the end
        esls.append(make_efi_signature_list(EFI_CERT_X509_GUID, [tmp]))

    return b"".join(esls)


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

        # fmt: off
        commands.local_cmd([
            'openssl', 'req', '-new', '-x509', '-newkey', 'rsa:2048',
            '-subj', '/CN=%s/' % common_name, '-nodes', '-keyout',
            key, '-sha256', '-days', '3650', '-out', pub
        ])
        # fmt: on

        return cls(pub, key)

    def sign_efi_sig_db(self, var: str, data: bytes, guid: Optional[uuid.UUID]):
        assert self.key is not None
        authvar, _, _, _ = make_efi_variable_authentication_2(
            var,
            guid if guid else SECURE_BOOT_VARIABLES[var],
            [data],
            timestamp(),
            EFI_VARIABLE_SECUREBOOT_KEYS,
            False,
            self.pub,
            self.key,
            _tempdir.get().name,
        )
        return authvar

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
        assert owner_cert is None or owner_cert.key is not None, "owner cert must have private key"
        self.name = name
        self.guid = SECURE_BOOT_VARIABLES[self.name]
        self._owner_cert = owner_cert
        self._other_certs = list(other_certs or [])
        self._efi_signature_list = self._get_efi_signature_list()
        # Byte contents of the authenticated variable
        self._auth_data = None
        # File path of the authenticated variable
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
            # fmt: off
            commands.local_cmd([
                'sbsign', '--key', self._owner_cert.key, '--cert', self._owner_cert.pub, image, '--output', signed
            ])
            # fmt: on
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
    # fmt: off
    return auth_data[auth_data.index(EFI_CERT_X509_GUID.bytes_le):]
    # fmt: on


def get_md5sum_from_auth(auth: str):
    return hashlib.md5(esl_from_auth_file(auth)).hexdigest()
