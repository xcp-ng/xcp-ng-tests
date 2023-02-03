import logging
import pytest

# Explicitly import package-scoped fixtures (see explanation in pkgfixtures.py)
from pkgfixtures import host_with_saved_yum_state

@pytest.fixture(scope="package")
def host_with_hvm_fep(host):
    logging.info("Checking for HVM FEP support")
    if 'hvm_fep' not in host.ssh(['xl', 'info', 'xen_commandline']).split():
        pytest.fail("HVM FEP is required for some of the XTF tests")
    yield host

@pytest.fixture(scope="package")
def host_with_dynamically_disabled_ept_sp(host):
    """
    Disable EPT superpages before running XTF.

    The XSA-304 POC will crash hosts with vulnerable hardware if EPT SP are enabled.
    """
    logging.info("Switching EPT superpages to secure")
    host.ssh(['xl', 'set-parameters', 'ept=no-exec-sp'])
    yield host
    logging.info("Switching back EPT superpages to fast")
    host.ssh(['xl', 'set-parameters', 'ept=exec-sp'])

@pytest.fixture(scope="package")
def host_with_git_and_gcc(host_with_saved_yum_state):
    host = host_with_saved_yum_state
    host.yum_install(['git', 'gcc'])
    yield host

@pytest.fixture(scope="package")
def xtf_runner(host_with_git_and_gcc):
    host = host_with_git_and_gcc
    logging.info("Download and build XTF")
    tmp_dir = host.ssh(['mktemp', '-d'])
    try:
        host.execute_script(f"""set -eux
cd {tmp_dir}
git clone git://xenbits.xen.org/xtf.git
cd xtf
make -j$(nproc)
""")
    except Exception:
        logging.info("Setup failed: delete temporary directory.")
        host.ssh(['rm', '-rf', tmp_dir])
        raise
    yield f"{tmp_dir}/xtf/xtf-runner"
    # teardown
    logging.info("Delete XTF")
    host.ssh(['rm', '-rf', tmp_dir])
