import pytest

import logging
import uuid

from lib.host import Host
from lib.tracing import Tracing

# Requirements:
# From --hosts parameter:
# - a XCP-ng host
# Optionally a --tracing-endpoint parameter with a reachable endpoint

class TestTracing:
    # A very simple test with optional tracing support
    def test_tracing(self, host: Host, tracing: Tracing):
        # do what needs to be tested but provide a trace identifier for the operations we want to trace
        operation = 'observer-list'
        tag1 = 'test.tag'
        tag1_value = str(uuid.uuid4())
        tag2 = 'testid'
        tag2_value = 'tests/storage/nfs/test_nfs_sr_intrapool_migration.py::Test::test_live_intrapool_shared_migration[None-vhd-vm_on_nfs4_sr]'
        logging.info(f'Peforming {operation} tagged with: {tag1} and {tag2}')
        host.xe(operation, vars={'BAGGAGE': {tag1: tag1_value, tag2: tag2_value}})
        # also works in string form and multiple env vars:
        # host.xe(operation, vars={'BAGGAGE': f'{tag1}={tag1_value};{tag2}={tag2_value}', 'TRACEPARENT': tag1_value})

        # functional validation of the test (but we are testing tracing itself here)
        assert True

        # output additional tracing stats
        if tracing.enabled:
            assert tracing.locate_span(operation, tag1, tag1_value), f'Could not find span with tag {tag1}'
            span = tracing.locate_span(operation, tag2, tag2_value)
            assert span, f'Could not find span with tag {tag2}'
            span_tag1_value = span.get("tags", {}).get(tag1)
            assert span_tag1_value == tag1_value, f'Tag value expected: {tag1_value}, actual: {span_tag1_value}'
            span_tag2_value = span.get("tags", {}).get(tag2)
            assert span_tag2_value == tag2_value, f'Tag value expected: {tag2_value}, actual: {span_tag2_value}'
            logging.info(f'{operation} span tag values: {span_tag1_value}, {span_tag2_value}')
