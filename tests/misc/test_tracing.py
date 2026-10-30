import pytest

import logging
import uuid

from lib.host import Host
from lib.tracing import Tracing

# Requirements:
# From --hosts parameter: a XCP-ng host
# A --tracing-endpoint parameter

class TestTracing:
    def test_tracing(self, host: Host, tracing: Tracing) -> None:
        # simple test with optional tracing support
        def observer_list(host: Host, tracing: Tracing) -> None:
            operation = 'observer-list'
            # traceparents allow to fetch spans from our operation's trace using the trace ID
            traceparent = tracing.gen_traceparent()
            # tags allow to attach arbitrary metadata to spans when provided in a baggage
            tag1 = 'test.uuid'
            tag1_value = str(uuid.uuid4())
            tag2 = 'test.id'
            tag2_value = 'tests/misc/test_tracing.py::TestTracing::test_tracing'
            # execute toolstack operation(s) with trace context
            logging.info(f'Peforming xe {operation} tagged with: {tag1},{tag2}')
            host.xe(operation, vars={'TRACEPARENT': traceparent, 'BAGGAGE': {tag1: tag1_value, tag2: tag2_value}})

            # functional validation of the test, nothing since we are testing tracing itself
            assert True

            if tracing.enabled:
                # output tracing stats
                traceid = traceparent.split("-", 3)[1]
                logging.info(f'xe {operation} trace ID: {traceid}')
                logging.info("Locating spans")
                span = tracing.span_from_traceid(f'xe {operation}', traceid)
                duration = span.get("duration")
                if duration:
                    logging.info(f'xe {operation} duration: {duration / 10**3}ms')

                # tracing testing, currently only http/s endpoints are supported
                if tracing.endpoint and 'http' in tracing.endpoint:
                    span_tag1_value = span.get("tags", {}).get(tag1)
                    assert span_tag1_value == tag1_value, f'Tag value expected: {tag1_value}, actual: {span_tag1_value}'
                    span_tag2_value = span.get("tags", {}).get(tag2)
                    assert span_tag2_value == tag2_value, f'Tag value expected: {tag2_value}, actual: {span_tag2_value}'

        if not tracing.enabled:
            # skipping for jobs which don't use tracing
            pytest.skip("Tracing wasn't enabled")
        observer_list(host, tracing)
        # repeat without flushing the tracing endpoint
        observer_list(host, tracing)
