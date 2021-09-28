def test_get_hyperthreading(host):
    host.call_plugin('hyperthreading.py', 'get_hyperthreading')
