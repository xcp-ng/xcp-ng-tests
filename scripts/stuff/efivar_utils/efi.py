import datetime
import logging
import struct
import subprocess
import uuid

EFI_CERT_X509_GUID = uuid.UUID("a5c059a1-94e4-4aa7-87b5-ab155c2bf072")
EFI_CERT_SHA256_GUID = uuid.UUID("c1c41626-504c-4092-aca9-41f936934328")

EFI_SIGNATURE_LIST = struct.Struct("<16sIII")

SVN_OWNER_GUID = uuid.UUID("9d132b6c-59d5-4388-ab1c-185cfcb2eb92")

EFI_VARIABLE_NON_VOLATILE = 0x00000001
EFI_VARIABLE_BOOTSERVICE_ACCESS = 0x00000002
EFI_VARIABLE_RUNTIME_ACCESS = 0x00000004
EFI_VARIABLE_TIME_BASED_AUTHENTICATED_WRITE_ACCESS = 0x00000020
EFI_VARIABLE_APPEND_WRITE = 0x00000040

EFI_TIME = struct.Struct("<HBBBBBBIhBB")
assert EFI_TIME.size == 16
EFI_TIME_APPEND = EFI_TIME.pack(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

WIN_CERTIFICATE_UEFI_GUID = struct.Struct("<IHH16s")
EFI_CERT_TYPE_PKCS7_GUID = uuid.UUID("4aafd29d-68df-49ee-8aa9-347d375665a7")
WIN_CERT_TYPE_EFI_GUID = 0x0EF1


def efi_time_to_timestamp(*args):
    return datetime.datetime(
        args[0],
        args[1],
        args[2],
        args[3],
        args[4],
        args[5],
        microsecond=args[7] // 1000,
        tzinfo=datetime.timezone(datetime.timedelta(minutes=args[8])) if args[8] != 2047 else None,
    )


def convert_certificate(infile, outfile):
    logging.info(f"converting {infile} -> {outfile}")
    cert_forms = ["PEM", "DER"]
    for inform in cert_forms:
        logging.info(f"trying {inform}")
        try:
            subprocess.run(
                [
                    "openssl",
                    "x509",
                    "-in",
                    str(infile),
                    "-inform",
                    inform,
                    "-outform",
                    "DER",
                    "-out",
                    str(outfile),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            logging.info("OK")
            break
        except subprocess.CalledProcessError:
            pass
    else:
        raise Exception(f"Cannot convert certificate file {infile}")
