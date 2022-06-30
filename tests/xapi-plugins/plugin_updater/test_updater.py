import json

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host > 8.2.
# And:
# - access to XCP-ng RPM repository from hostA1

class TestUpdate:
    def test_check_update(self, host):
        host.call_plugin('updater.py', 'check_update')

    def test_update(self, host):
        host.yum_save_state()
        host.call_plugin('updater.py', 'update')

        res = host.call_plugin('updater.py', 'check_update')
        assert res == "[]"

        host.yum_restore_saved_state()

class TestProxies:
    def test_get_proxies(self, host):
        proxies = json.loads(host.call_plugin('updater.py', 'get_proxies'))
        for repo in 'xcp-ng-base', 'xcp-ng-testing', 'xcp-ng-updates':
            assert repo in proxies
