# Requirements:
# From --hosts parameter:
# - host(A1): any xcp-ng host

def test_linux_image_perf_probe(host_with_perf):
    host_with_perf.ssh(['perf', 'probe', '--add', 'xenbus_backend_ioctl'])
    host_with_perf.ssh(['perf', 'record', '-e', 'probe:xenbus_backend_ioctl', '-aR', 'sleep', '1'])
    host_with_perf.ssh(['perf', 'report'])
