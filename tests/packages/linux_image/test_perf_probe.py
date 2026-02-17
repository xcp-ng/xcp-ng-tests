# Requirements:
# From --hosts parameter:
# - host(A1): any xcp-ng host
import re

from lib.host import Host

def perf_probe(host: Host, probe: str) -> list[str]:
    host.ssh(f'perf probe --add {probe}')
    host.ssh(f'perf record -e probe:{probe} -o ~/perf.data -aR -- sleep 10')
    host.ssh(f'perf probe --del {probe}')

    samples = host.ssh('perf report -i ~/perf.data -D --stdio-color never')
    host.ssh('rm ~/perf.data')

    return re.findall(r'RECORD_SAMPLE', samples)

def test_linux_image_perf_probe(host_with_perf: Host) -> None:
    # Probe that triggers very often:
    probe = 'xen_flush_tlb_one_user'

    match = perf_probe(host_with_perf, probe)

    assert len(match) > 0, "No sample found for probe %s!" % probe

    # Example of a function that isn't called often:
    probe = 'xenbus_backend_ioctl'

    match = perf_probe(host_with_perf, probe)

    assert len(match) == 0, "Samples found for probe %s!" % probe
