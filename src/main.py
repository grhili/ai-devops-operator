#!/usr/bin/env python3
"""
AI Operator - Main Entry Point

This operator reconciles GitHub pull requests based on AI-driven decisions
defined in PRReconciliationRule CRDs.
"""

import asyncio
import os
import signal
import sys
from typing import Optional

import structlog

from reconciler import Reconciler

# Configure structured logging
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
    """Handle graceful shutdown on SIGTERM/SIGINT"""

    def __init__(self):
        self.shutdown_requested = False
        signal.signal(signal.SIGTERM, self.request_shutdown)
        signal.signal(signal.SIGINT, self.request_shutdown)

    def request_shutdown(self, signum, frame):
        logger.info("shutdown_requested", signal=signum)
        self.shutdown_requested = True


async def main():
    """Main entry point"""
    logger.info(
        "ai_operator_starting",
        version="0.1.0",
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        namespace=os.getenv("NAMESPACE", "default"),
    )

    # Set up graceful shutdown
    shutdown_handler = GracefulShutdown()

    # Initialize reconciler
    try:
        reconciler = Reconciler()
        await reconciler.initialize()
    except Exception as e:
        logger.error("reconciler_initialization_failed", error=str(e), exc_info=True)
        sys.exit(1)

    # Run reconciliation loop
    try:
        await reconciler.run(shutdown_handler)
    except Exception as e:
        logger.error("reconciler_loop_failed", error=str(e), exc_info=True)
        sys.exit(1)
    finally:
        await reconciler.shutdown()
        logger.info("ai_operator_stopped")


if __name__ == "__main__":
    asyncio.run(main())
