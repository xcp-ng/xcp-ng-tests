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
        operation = 'observer-list'
        # unique traceparent IDs help to review relevant traces if a surprising result comes up
        traceparent = tracing.gen_traceparent()
        # unique span tag values allow to run the same test multiple times without flushing the endpoint
        tag1 = 'test.uuid'
        tag1_value = str(uuid.uuid4())
        # span tags allow to attach arbitrary useful data
        tag2 = 'test.id'
        tag2_value = 'tests/storage/nfs/test_nfs_sr_intrapool_migration.py::Test::test_live_intrapool_shared_migration[None-vhd-vm_on_nfs4_sr]'
        logging.info(f'Peforming xe {operation} tagged with: {tag1} and {tag2}')
        host.xe(operation, vars={'TRACEPARENT': traceparent, 'BAGGAGE': {tag1: tag1_value, tag2: tag2_value}})
        # above call also works in string form:
        # host.xe(operation, vars={'TRACEPARENT': traceparent, 'BAGGAGE': f'{tag1}={tag1_value};{tag2}={tag2_value}'})

        # functional validation of the test, nothing here since we are testing tracing itself
        assert True

        # output tracing stats
        if tracing.enabled:
            logging.info(f'xe {operation} trace ID: {traceparent.split("-", 3)[1]}')
            span = tracing.locate_span(f'xe {operation}', tag1, tag1_value)
            assert span, f"Couldn't find a span with tag {tag1}"
            span_tag1_value = span.get("tags", {}).get(tag1)
            assert span_tag1_value == tag1_value, f'Tag value expected: {tag1_value}, actual: {span_tag1_value}'
            span_tag2_value = span.get("tags", {}).get(tag2)
            assert span_tag2_value == tag2_value, f'Tag value expected: {tag2_value}, actual: {span_tag2_value}'
            logging.info(f'xe {operation} duration: {span.get("duration") / 1000}ms')
