from __future__ import annotations

import logging
import string
import time
from random import choice
from urllib.parse import urlparse

import requests

from typing import Any

class Tracing:
    def __init__(self, enabled: bool, endpoint: str | None) -> None:
        self.enabled = enabled
        self.endpoint = endpoint

    def gen_traceparent(self) -> str:
        # W3C: https://www.w3.org/TR/trace-context/#traceparent-header
        version = "00"
        trace_id = ''.join(choice(string.hexdigits) for _ in range(32)).lower()
        parent_id = ''.join(choice(string.hexdigits) for _ in range(16)).lower()
        trace_flags = "01"
        return '-'.join([version, trace_id, parent_id, trace_flags])

    def span_from_traceid(self, name: str, traceid: str) -> dict[str, Any]:
        def get_span_zipkinv2(name: str) -> dict[str, Any]:
            # case-sensitive endpoint with only span names converted to lowercase
            name = name.lower()
            url = urlparse(self.endpoint)
            response = requests.get(f'{url.scheme}://{url.netloc}/api/v2/trace/{traceid}')
            if response.status_code == 200:
                # Jaeger's zipkin v2 JSON is not supported
                trace = response.json()
                for span in trace:
                    if span.get('name') == name:
                        return span
            return {}

        if not self.enabled:
            return {}
        logging.debug(f'Searching span {name} using trace ID {traceid}')
        span = {}
        retries = 12
        for _ in range(retries):
            if self.endpoint and "api/v2/spans" in self.endpoint:
                span = get_span_zipkinv2(name)
            else:
                logging.warning("Tracing endpoint type not supported")
                break
            if span != {}:
                return span
            time.sleep(5)

        logging.debug(f'Span {name} not found')
        return span

    def stats_migration(self, traceid: str) -> None:
        """ Prints a migration's general durations from a trace ID. """
        if not self.enabled:
            return
        logging.info(f'Migration trace ID: {traceid}')
        logging.info("Locating migration spans")
        span_migration = self.span_from_traceid("VM.pool_migrate", traceid)
        if span_migration == {}:
            # retry with cross-pool migration span
            span_migration = self.span_from_traceid("VM.migrate_send", traceid)
        duration = span_migration.get("duration")
        if duration:
            logging.info(f'Migration duration: {duration / 10**6}s')
        span_downtime_begin = self.span_from_traceid("VM_migrate_downtime_begin", traceid)
        span_downtime_end = self.span_from_traceid("VM_migrate_downtime_end", traceid)
        downtime_start = span_downtime_begin.get("timestamp")
        downtime_end = span_downtime_end.get("timestamp")
        if downtime_start and downtime_end:
            logging.info(f'VM downtime duration: {(downtime_end - downtime_start) / 10**6}s')
