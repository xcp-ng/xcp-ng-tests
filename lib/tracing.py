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
        self.enabled: bool = enabled
        self.endpoint: str | None = endpoint

    def gen_traceparent(self) -> str:
        # W3C: https://www.w3.org/TR/trace-context/#traceparent-header
        version = "00"
        trace_id = ''.join(choice(string.hexdigits) for _ in range(32)).lower()
        parent_id = ''.join(choice(string.hexdigits) for _ in range(16)).lower()
        trace_flags = "01"
        return '-'.join([version, trace_id, parent_id, trace_flags])

    def span_from_traceid(self, name: str, traceid: str) -> Any:
        def get_span_zipkinv2(url: Any, name: str) -> Any:
            # case-sensitive endpoint with only span names converted to lowercase
            name = name.lower()
            response = requests.get(url)
            if response.status_code == 200:
                trace = response.json()
                for span in trace:
                    if span.get('name') == name:
                        return span
            return None

        if not self.enabled:
            return None
        span = None
        retries = 12
        for _ in range(retries):
            # only true zipkin v2 endpoints are supported (Jaeger's zipkin v2 port is not supported)
            if self.endpoint and "api/v2/spans" in self.endpoint:
                url = urlparse(self.endpoint)
                span = get_span_zipkinv2(f"{url.scheme}://{url.netloc}/api/v2/trace/{traceid}", name)
            else:
                logging.warning(f"tracing endpoint type not supported")
                break
            if not span:
                time.sleep(5)
            else:
                break
        if not span:
            logging.debug(f"Span {name} not found")
        return span

    def stats_migration(self, traceparent) -> None:
        """ Common method to summarize the migration durations identified by the traceparent. """
        if not self.enabled:
            return
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
