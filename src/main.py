#!/usr/bin/env python3
"""
AI Operator - Main Entry Point
"""

import asyncio
import os
import signal
import sys

import structlog
from aiohttp import web

from metrics import start_metrics_server
from reconciler import Reconciler

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
        if os.getenv("LOG_FORMAT", "json") == "json"
        else structlog.dev.ConsoleRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


class GracefulShutdown:
    def __init__(self):
        self.shutdown_requested = False
        signal.signal(signal.SIGTERM, self._handle)
        signal.signal(signal.SIGINT, self._handle)

    def _handle(self, signum, frame):
        logger.info("shutdown_requested", signal=signum)
        self.shutdown_requested = True


# ------------------------------------------------------------------
# Health / readiness server
# ------------------------------------------------------------------

class HealthServer:
    """
    Minimal aiohttp server for Kubernetes liveness and readiness probes.

    /healthz  — liveness:  always 200 once the process is up
    /ready    — readiness: 200 once the reconciler has successfully initialised,
                           503 before that
    """

    def __init__(self, port: int = 8080):
        self._port = port
        self._ready = False
        self._runner: web.AppRunner | None = None

    def mark_ready(self) -> None:
        self._ready = True

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/healthz", self._healthz)
        app.router.add_get("/ready", self._ready_handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await site.start()
        logger.info("health_server_started", port=self._port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    async def _healthz(self, _request: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def _ready_handler(self, _request: web.Request) -> web.Response:
        if self._ready:
            return web.Response(text="ok")
        return web.Response(status=503, text="not ready")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

async def main() -> None:
    logger.info("ai_operator_starting", version="0.1.0",
                namespace=os.getenv("NAMESPACE", "default"))

    shutdown = GracefulShutdown()

    # Start metrics server (Prometheus)
    metrics_port = int(os.getenv("METRICS_PORT", "9090"))
    start_metrics_server(metrics_port)

    # Start health server (liveness / readiness probes)
    health_port = int(os.getenv("HEALTH_PORT", "8080"))
    health = HealthServer(port=health_port)
    await health.start()

    # Initialise reconciler
    try:
        reconciler = Reconciler()
        await reconciler.initialize()
    except Exception as exc:
        logger.error("reconciler_initialization_failed", error=str(exc), exc_info=True)
        await health.stop()
        sys.exit(1)

    health.mark_ready()

    # Run reconciliation loop
    try:
        await reconciler.run(shutdown)
    except Exception as exc:
        logger.error("reconciler_loop_failed", error=str(exc), exc_info=True)
        sys.exit(1)
    finally:
        await reconciler.shutdown()
        await health.stop()
        logger.info("ai_operator_stopped")


if __name__ == "__main__":
    asyncio.run(main())
