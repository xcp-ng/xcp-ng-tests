from __future__ import annotations

import string
import time
from random import choice
from urllib.parse import urlparse

import requests

from typing import Any

import logging

class Tracing:
    def __init__(self, endpoint, enabled) -> None:
        self.endpoint: str | None = endpoint
        self.enabled: bool = enabled
        # NOTE an observer uuid could be passed by the tracing fixture if needed by some tests, but not needed for now

    def gen_traceparent(self) -> str:
        # W3C: https://www.w3.org/TR/trace-context/#traceparent-header
        version = "00"
        trace_id = ''.join(choice(string.hexdigits) for _ in range(32)).lower()
        parent_id = ''.join(choice(string.hexdigits) for _ in range(16)).lower()
        trace_flags = "01"
        return '-'.join([version, trace_id, parent_id, trace_flags])

    def get_span_zipkinv2(self, name: str, traceid: str) -> Any:
        url = urlparse(self.endpoint)
        # only true zipkin endpoints are supported (Jaeger zipkin port not supported)
        # case-sensitive endpoint with only span names converted to lowercase
        name = name.lower()
        retries = 12
        for _ in range(retries):
            response = requests.get(f"{url.scheme}://{url.netloc}/api/v2/trace/{traceid}")
            if response.status_code == 200:
                trace = response.json()
                for span in trace:
                    if span.get('name') == name:
                        return span
            time.sleep(5)
        return None

    def span_from_traceid(self, name: str, traceid: str) -> Any:
        if not self.enabled or not self.endpoint:
            return None
        span = None
        if "api/v2/spans" in self.endpoint:
            span = self.get_span_zipkinv2(name, traceid)
        else:
            logging.warning(f"tracing endpoint type not supported")

        if span:
            logging.debug(f"Span {name} found")
        else:
            logging.debug(f"Span {name} not found")
        return span

    # NOTE allows to not duplicate this code in each migration test, which will be useful
    # as we will probably need to evolve the migration spans search in the future.
    def stats_migration(self, traceparent) -> None:
        """ Storage migration stats not tested (VDI.pool_migrate). """
        if not self.enabled or not self.endpoint:
            return
        # NOTE some migration spans are missing the user tags (like VM_migrate_downtime_end),
        # it likely depends on how/where the with_tracing call are done in xapi/xenopsd
        traceid = traceparent.split("-", 3)[1]
        logging.info(f'Migration trace ID: {traceid}')
        logging.info("Locating migration spans")
        span_migration = self.span_from_traceid("VM.pool_migrate", traceid)
        if not span_migration:
            # retry with cross-pool migration span
            span_migration = self.span_from_traceid("VM.migrate_send", traceid)
        span_downtime_begin = self.span_from_traceid("VM_migrate_downtime_begin", traceid)
        span_downtime_end = self.span_from_traceid("VM_migrate_downtime_end", traceid)
        if span_migration:
            logging.info(f'Migration duration: {span_migration.get("duration") / 1000000}s')
        if span_downtime_begin and span_downtime_end:
            logging.info(
                f'Downtime duration: {(span_downtime_end.get("timestamp") - span_downtime_begin.get("timestamp")) / 1000000}s')
