import os

import pytest

from lib.commands import ssh

# tests meant to check system logs for error right after installation
# (not after other tests which may willingly cause such errors)

@pytest.mark.parametrize("logfile", ("xensource.log", "daemon.log", "SMlog"))
def test_logs(host, logfile):
    LOGFILE = os.path.join("/var/log", logfile)

    result = host.ssh(["grep -B1 -Ei 'error|fail|fatal|critical|No such file'", LOGFILE],
                      check=False, simple_output=False)
    assert result.returncode in (0, 1), "error in 'ssh grep'"
    # assert result.returncode == 1, f"Errors in {logfile}, see debug output"
    assert result.returncode == 0, f"It is NOT NORMAL AT ALL to have no error in {logfile}"

# FIXME too many false positives to be a real test, essentially useful
# to get the matches extracted and archived for manual verification
def test_install_logs(host):
    LOGFILE = "/var/log/installer/install-log"

    result = host.ssh(["grep -B1 -Ei 'error|fail|fatal|critical|No such file'", LOGFILE],
                      check=False, simple_output=False)
    assert result.returncode in (0, 1), "error in 'ssh grep'"
    # assert result.returncode == 1, f"Errors in {LOGFILE}, see debug output"
    assert result.returncode == 0, f"It is NOT NORMAL AT ALL to have no error in {LOGFILE}"
