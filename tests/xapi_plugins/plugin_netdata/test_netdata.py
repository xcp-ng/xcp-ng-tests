import pytest

from lib.common import strtobool

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host > 8.2.
# And:
# - access to XCP-ng RPM repository from hostA1

@pytest.fixture(scope='module')
def host_without_netdata(host):
    assert not strtobool(host.call_plugin('netdata.py', 'is_netdata_installed'))
    yield host

class TestInstall:
    def test_is_netdata_installed(self, host):
        host.call_plugin('netdata.py', 'is_netdata_installed')

    def test_install_netdata(self, host_without_netdata):
        host = host_without_netdata
        host.yum_save_state()
        host.call_plugin('netdata.py', 'install_netdata', {
            "api_key": "dummy_key", "destination": "127.0.0.1:19999"
        })

        assert strtobool(host.call_plugin('netdata.py', 'is_netdata_installed'))

        host.yum_restore_saved_state()

class TestApiKey:
    def test_get_netdata_api_key(self, host_without_netdata):
        host = host_without_netdata
        host.yum_save_state()
        host.call_plugin('netdata.py', 'install_netdata', {
            "api_key": "dummy_key", "destination": "127.0.0.1:19999"
        })

        res = host.call_plugin('netdata.py', 'get_netdata_api_key')
        assert res == "dummy_key"

        host.yum_restore_saved_state()
