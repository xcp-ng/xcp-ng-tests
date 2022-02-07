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
        host.call_plugin('updater.py', 'get_proxies')

    # TODO: test more set with URLs etc and trigger errors

    def test_set_proxies(self, host):
        proxies = host.call_plugin('updater.py', 'get_proxies')

        set_proxies = '{ \
            "xcp-ng-base": "_none_", \
            "xcp-ng-updates": "_none_", \
            "xcp-ng-testing": "_none_", \
            "xcp-ng-staging": "_none_" \
        }'
        host.call_plugin('updater.py', 'set_proxies', {"proxies": set_proxies}, use_scp=True)

        res = host.call_plugin('updater.py', 'get_proxies')
        assert json.loads(res) == json.loads(set_proxies)

        host.call_plugin('updater.py', 'set_proxies', {"proxies": proxies}, use_scp=True)
        assert json.loads(res) == json.loads(proxies)
