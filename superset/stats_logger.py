# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
import logging
from typing import Any, Optional

from colorama import Fore, Style
from flask import Response

logger = logging.getLogger(__name__)


class BaseStatsLogger:
    """Base class for logging realtime events"""

    def __init__(self, prefix: str = "superset") -> None:
        self.prefix = prefix

    def key(self, key: str) -> str:
        if self.prefix:
            return self.prefix + key
        return key

    def incr(self, key: str) -> None:
        """Increment a counter"""
        raise NotImplementedError()

    def decr(self, key: str) -> None:
        """Decrement a counter"""
        raise NotImplementedError()

    def timing(self, key: str, value: float) -> None:
        raise NotImplementedError()

    def gauge(self, key: str, value: float) -> None:
        """Setup a gauge"""
        raise NotImplementedError()
    
    def write(self) -> Optional[Response]:
        """Write statistics to a response, if possible"""
        return None


class DummyStatsLogger(BaseStatsLogger):
    def incr(self, key: str) -> None:
        logger.debug("%s[stats_logger] (incr) %s%s", Fore.CYAN, key, Style.RESET_ALL)

    def decr(self, key: str) -> None:
        logger.debug("%s[stats_logger] (decr) %s%s", Fore.CYAN, key, Style.RESET_ALL)

    def timing(self, key: str, value: float) -> None:
        logger.debug(
            "%s[stats_logger] (timing) %s | %s %s",
            Fore.CYAN,
            key,
            value,
            Style.RESET_ALL,
        )

    def gauge(self, key: str, value: float) -> None:
        logger.debug(
            "%s[stats_logger] (gauge) %s%s%s", Fore.CYAN, key, value, Style.RESET_ALL
        )


try:
    from statsd import StatsClient

    class StatsdStatsLogger(BaseStatsLogger):
        def __init__(  # pylint: disable=super-init-not-called
            self,
            host: str = "localhost",
            port: int = 8125,
            prefix: str = "superset",
            statsd_client: Optional[StatsClient] = None,
        ) -> None:
            """
            Initializes from either params or a supplied, pre-constructed statsd client.

            If statsd_client argument is given, all other arguments are ignored and the
            supplied client will be used to emit metrics.
            """
            if statsd_client:
                self.client = statsd_client
            else:
                self.client = StatsClient(host=host, port=port, prefix=prefix)

        def incr(self, key: str) -> None:
            self.client.incr(key)

        def decr(self, key: str) -> None:
            self.client.decr(key)

        def timing(self, key: str, value: float) -> None:
            self.client.timing(key, value)

        def gauge(self, key: str, value: float) -> None:
            self.client.gauge(key, value)

except Exception as e:  # pylint: disable=broad-except  # noqa: S110
    # e can only be accessed in the catch and not later during class instantiation.
    # We have to save it to a separate variable.
    _saved_exception = e

    class StatsdStatsLogger(BaseStatsLogger):  # type:ignore[no-redef] # the redefinition only happens when the original definition failed
        def __init__(  # pylint: disable=super-init-not-called
            self,
            host: str = "localhost",
            port: int = 8125,
            prefix: str = "superset",
            statsd_client: Any = None,
        ) -> None:
            """
            Initializes from either params or a supplied, pre-constructed statsd client.

            If statsd_client argument is given, all other arguments are ignored and the
            supplied client will be used to emit metrics.

            If an exception is raised while creating the StatsdStatsLogger class, for
            example because the statsd package is not installed, it will be re-raised
            on instantiation of the StatsdStatsLogger.
            """
            raise _saved_exception

try:
    import re

    from prometheus_client import (
        CollectorRegistry,
        CONTENT_TYPE_LATEST,
        Counter,
        Gauge,
        generate_latest,
        Histogram,
        multiprocess,
    )

    class PrometheusStatsLogger(BaseStatsLogger):

        metrics = {}

        def __init__(  # pylint: disable=super-init-not-called
            self,
            prefix,
            multiprocess_mode: bool = False,
            registry: Optional[CollectorRegistry] = None,
        ) -> None:
            """
            Initializes from either params or a supplied, pre-constructed statsd client.
            If statsd_client argument is given, all other arguments are ignored and the
            supplied client will be used to emit metrics.
            """
            super().__init__(prefix)
            self.multiprocess_mode = multiprocess_mode
            if registry:
                self.registry = registry
            else:
                self.registry = CollectorRegistry()
                if multiprocess_mode:
                    multiprocess.MultiProcessCollector(self.registry)

        def extract_metric_name_and_tags(
            self, key: str
        ) -> tuple[str, list[str], list[str]]:
            key = key.replace(".", "_")
            api_request_match = re.search(r"^(.*)RestApi_(.*)_([^_]*)$", key)
            if api_request_match:
                api_group = api_request_match.group(1)
                api_endpoint = api_request_match.group(2)
                api_type = api_request_match.group(3)
                if api_type == "time":
                    return (
                        "http_server_requests_seconds",
                        ["api_group", "api_endpoint"],
                        [api_group, api_endpoint],
                    )
                return (
                    "http_server_requests",
                    ["api_group", "api_endpoint", "response_type"],
                    [api_group, api_endpoint, api_type],
                )
            sqllab_match = re.search(r"^sqllab_query(?:_time)?_(.*)$", key)
            if sqllab_match:
                return (
                    self.prefix + "_sqllab_query_seconds",
                    ["operation"],
                    [sqllab_match.group(1)],
                )
            return self.prefix + "_" + key, [], []

        def getCounter(self, key: str) -> Counter:
            name, tag_keys, tag_values = self.extract_metric_name_and_tags(key)
            if name not in self.metrics:
                self.metrics[name] = Counter(name, "auto-generated", tag_keys)
            if len(tag_values):
                return self.metrics[name].labels(*tag_values)
            return self.metrics[name]

        def getGauge(self, key: str) -> Gauge:
            name, tag_keys, tag_values = self.extract_metric_name_and_tags(key)
            if name not in self.metrics:
                self.metrics[name] = Gauge(name, "auto-generated", tag_keys)
            if len(tag_values):
                return self.metrics[name].labels(*tag_values)
            return self.metrics[name]

        def getTimer(self, key: str) -> Histogram:
            name, tag_keys, tag_values = self.extract_metric_name_and_tags(key)
            if name not in self.metrics:
                self.metrics[name] = Histogram(name, "auto-generated", tag_keys)
            if len(tag_values):
                return self.metrics[name].labels(*tag_values)
            return self.metrics[name]

        def incr(self, key: str) -> None:
            self.getCounter(key).inc()

        def decr(self, key: str) -> None:
            logger.warning("[stats_logger] (decr) " + key)
            self.getGauge(key).dec()

        def timing(self, key: str, value: float) -> None:
            self.getTimer(key).observe(value / 1000)

        def gauge(self, key: str, value: float) -> None:
            self.getGauge(key).set(value)

        def write(self):
            return Response(
                generate_latest(self.registry), mimetype=CONTENT_TYPE_LATEST
            )

except Exception as e:  # pylint: disable=broad-except  # noqa: S110
    saved_exception = e

    class PrometheusStatsLogger(BaseStatsLogger):
        def __init__(
            self,
            prefix,
            multiprocess_mode=False,
            registry=None,
        ) -> None:  # pylint: disable=super-init-not-called
            raise saved_exception


class MultiLogger(BaseStatsLogger):
    def __init__(self, *loggers: BaseStatsLogger) -> None:
        self.loggers = loggers

    def incr(self, key: str) -> None:
        for logger in self.loggers:
            logger.incr(key)

    def decr(self, key: str) -> None:
        for logger in self.loggers:
            logger.decr(key)

    def timing(self, key: str, value: float) -> None:
        for logger in self.loggers:
            logger.timing(key, value)

    def gauge(self, key: str, value: float) -> None:
        for logger in self.loggers:
            logger.gauge(key, value)

    def write(self) -> Optional[Response]:
        for logger in self.loggers:
            result = logger.write()
            if result:
                return result
        return None