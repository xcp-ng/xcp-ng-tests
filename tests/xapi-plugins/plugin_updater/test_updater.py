from pkgfixtures import host_with_saved_yum_state
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

    def test_package_update(self, host_with_saved_yum_state):
        host = host_with_saved_yum_state
        packages = host.get_available_package_versions('dummypkg')
        assert len(packages) == 2
        assert packages[0].startswith('dummypkg-0:1.0-1.xcpng')
        assert packages[1].startswith('dummypkg-0:1.0-2.xcpng')

        assert not host.is_package_installed(packages[0])
        host.call_plugin('updater.py', 'install', {'packages': packages[0]})
        assert host.is_package_installed(packages[0])

        host.call_plugin('updater.py', 'update', {'packages': 'dummypkg'})
        assert host.is_package_installed(packages[1])

class TestProxies:
    def test_get_proxies(self, host):
        proxies = json.loads(host.call_plugin('updater.py', 'get_proxies'))
        for repo in 'xcp-ng-base', 'xcp-ng-testing', 'xcp-ng-updates':
            assert repo in proxies
