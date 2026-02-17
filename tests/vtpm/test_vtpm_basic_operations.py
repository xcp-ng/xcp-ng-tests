import pytest

import logging

import lib.commands as commands
from lib.vm import VM

# These tests are basic tests for vTPM devices.
#   - Create / Destroy a vTPM device on a VM
#   - Do some basic encryption tests using tpm2-tools package
#
# Requirements:
# - an XCP-ng host >= 8.3
# - a RPM-based or DEB-based UEFI VM with `tpm2-tools` installable
#   from default repositories

vtpm_signing_test_script = """#!/bin/env bash

set -ex

tpm2_selftest --fulltest

tpm2_getrandom 32 > /dev/null

TMPDIR=`mktemp -d`

# Create an Endorsement primary key
tpm2_createprimary --hierarchy e --key-context ${TMPDIR}/primary.ctx > /dev/null

# Create key objects
tpm2_create --key-algorithm rsa --public ${TMPDIR}/rsa.pub --private ${TMPDIR}/rsa.priv --parent-context \
            ${TMPDIR}/primary.ctx > /dev/null

# Load keys into the TPM
tpm2_load --parent-context ${TMPDIR}/primary.ctx --public ${TMPDIR}/rsa.pub --private ${TMPDIR}/rsa.priv \
          --key-context ${TMPDIR}/rsa.ctx > /dev/null

# Delete loaded key files
rm -f ${TMPDIR}/rsa.pub ${TMPDIR}/rsa.priv

# Message to sign
echo 'XCP-ng Rulez' > ${TMPDIR}/message.dat

# Sign the message
tpm2_sign --key-context ${TMPDIR}/rsa.ctx --hash-algorithm sha256 --signature ${TMPDIR}/sig.rssa \
          ${TMPDIR}/message.dat > /dev/null

# Verify signature
tpm2_verifysignature --key-context ${TMPDIR}/rsa.ctx --hash-algorithm sha256 --message ${TMPDIR}/message.dat \
                     --signature ${TMPDIR}/sig.rssa > /dev/null

# Verify with another message
echo "XCP-ng Still Rulez" > ${TMPDIR}/message.dat

# Verify signature !!!!! THIS MUST FAIL !!!!!
if tpm2_verifysignature --key-context ${TMPDIR}/rsa.ctx --hash-algorithm sha256 --message ${TMPDIR}/message.dat \
                        --signature ${TMPDIR}/sig.rssa > /dev/null 2>&1; then
    echo "Should not succeed"
    exit 1
fi

rm -rf ${TMPDIR}
"""

@pytest.mark.small_vm
@pytest.mark.usefixtures("host_at_least_8_3")
def test_create_and_destroy_vtpm(halted_uefi_unix_vm: VM) -> None:
    vm = halted_uefi_unix_vm
    image_has_vtpm = vm.get_vtpm_uuid()
    try:
        if image_has_vtpm:
            vm.destroy_vtpm()
            assert not vm.get_vtpm_uuid(), "there must be no vTPM after we deleted it"
        assert not vm.get_vtpm_uuid(), "there must be no vTPM before we create it"
        vtpm_uuid = vm.create_vtpm()
        assert vtpm_uuid
        assert vm.get_vtpm_uuid(), "a vTPM must be present after creation"
        logging.info("vTPM created with uuid: %s" % vtpm_uuid)
        vm.destroy_vtpm()
        assert not vm.get_vtpm_uuid(), "there must be no vTPM after we deleted it"
    finally:
        if image_has_vtpm and not vm.get_vtpm_uuid():
            vm.create_vtpm()
        elif not image_has_vtpm and vm.get_vtpm_uuid():
            vm.destroy_vtpm()

@pytest.mark.small_vm
@pytest.mark.usefixtures("host_at_least_8_3")
def test_vtpm(unix_vm_with_tpm2_tools: VM) -> None:
    global vtpm_signing_test_script
    vm = unix_vm_with_tpm2_tools

    # Basic TPM2 tests with tpm2-tools
    logging.info("Running TPM2 test script on the VM")
    vm.execute_script(vtpm_signing_test_script)
