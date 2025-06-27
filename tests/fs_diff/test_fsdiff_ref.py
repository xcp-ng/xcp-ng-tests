import logging
import os
import pytest
import subprocess

from lib.commands import local_cmd

# Requirements:
# - 1 XCP-ng host

MYDIR = os.path.dirname(__file__)
REF_DIR = os.path.join(MYDIR, "data")
FSDIFF = os.path.realpath(f"{MYDIR}/../../scripts/xcpng-fs-diff.py")

def _ref_name(host):
    return f"{host.inventory['PRODUCT_VERSION']}-{host.firmware_type()}"

# FIXME: not a test, rather a task done on a given (cached) host
def test_fsdiff_mkref(host):
    logging.info("Extracting %s reference data from %s",
                 _ref_name(host), host.hostname_or_ip)
    process = local_cmd([FSDIFF,
                         "--reference-host", host.hostname_or_ip,
                         "--save-reference", os.path.join(REF_DIR, _ref_name(host)),
                         ])

def test_fsdiff_against_ref(host):
    ref_file = os.path.join(REF_DIR, _ref_name(host))
    logging.info("Comparing %s with reference data", host.hostname_or_ip)
    process = local_cmd([FSDIFF,
                         "--load-reference", ref_file,
                         "--test-host", host.hostname_or_ip,
                         ])
