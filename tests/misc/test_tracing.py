import pytest

import logging
import uuid

from lib.host import Host
from lib.tracing import Tracing

# Requirements:
# From --hosts parameter: a XCP-ng host
# A --tracing-endpoint parameter

class TestTracing:
    def test_tracing(self, host: Host, tracing: Tracing):
        def observer_list(host: Host, tracing: Tracing):
            operation = 'observer-list'
            # traceparents allow to fetch spans from our operation's trace using the trace ID
            traceparent = tracing.gen_traceparent()
            # tags allow to attach arbitrary metadata to spans when provided in a baggage
            tag1 = 'test.uuid'
            tag1_value = str(uuid.uuid4())
            tag2 = 'test.id'
            tag2_value = 'tests/misc/test_tracing.py::TestTracing::test_tracing'

            logging.info(f'Peforming xe {operation} tagged with: {tag1},{tag2}')
            host.xe(operation, vars={'TRACEPARENT': traceparent, 'BAGGAGE': {tag1: tag1_value, tag2: tag2_value}})
            # string form: vars={'TRACEPARENT': traceparent, 'BAGGAGE': f'{tag1}={tag1_value};{tag2}={tag2_value}'})

            # functional validation of the test, nothing since we are testing tracing itself
            assert True

            # output tracing stats
            traceid = traceparent.split("-", 3)[1]
            # print trace ID to manually review the trace in case a surprising result comes up
            logging.info(f'xe {operation} trace ID: {traceid}')
            logging.info("Locating spans")
            span = tracing.span_from_traceid(f'xe {operation}', traceid)
            # currently only http/s endpoints are supported
            if tracing.endpoint and 'http' in tracing.endpoint:
                assert span, f"Couldn't fetch root span"
                span_tag1_value = span.get("tags", {}).get(tag1)
                assert span_tag1_value == tag1_value, f'Tag value expected: {tag1_value}, actual: {span_tag1_value}'
                span_tag2_value = span.get("tags", {}).get(tag2)
                assert span_tag2_value == tag2_value, f'Tag value expected: {tag2_value}, actual: {span_tag2_value}'
                logging.info(f'xe {operation} duration: {span.get("duration") / 1000}ms')

        if not tracing.enabled:
            pytest.skip("missing --tracing-endpoint option")
        observer_list(host, tracing)
        # repeat the test without flushing the tracing endpoint
        observer_list(host, tracing)
