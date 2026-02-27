"""
Argo CD REST client (optional integration).

Argo CD exposes a REST API at /api/v1 — there is no official Python SDK,
so aiohttp against the REST API is the right approach here.

Set argocdEnabled: false in the PRReconciliationRule CRD to skip this entirely.
"""

from typing import Optional

import aiohttp
import structlog

logger = structlog.get_logger()


class ArgoCDClient:
    """
    Thin async client for Argo CD application health queries.

    Only instantiated when argocdEnabled is true on a rule.
    The session is shared for the operator's lifetime.
    """

    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
        self._token = token

    async def initialize(self) -> None:
        self._session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
        )
        logger.info("argocd_client_initialized", url=self.url)

    async def shutdown(self) -> None:
        if self._session:
            await self._session.close()

    async def get_application_health(self, app_name: str) -> Optional[str]:
        """
        Return the Argo CD health status string for `app_name`,
        e.g. "Healthy", "Degraded", "Progressing", "Unknown".
        Returns None on any error so callers can treat it as unknown.
        """
        if not self._session:
            logger.error("argocd_client_not_initialized")
            return None

        url = f"{self.url}/api/v1/applications/{app_name}"
        try:
            async with self._session.get(url) as resp:
                resp.raise_for_status()
                data = await resp.json()
                health = data.get("status", {}).get("health", {}).get("status", "Unknown")
                logger.debug("argocd_health", app=app_name, health=health)
                return health
        except aiohttp.ClientResponseError as exc:
            logger.error("argocd_http_error", app=app_name, status=exc.status, message=exc.message)
            return None
        except Exception as exc:
            logger.error("argocd_request_failed", app=app_name, error=str(exc))
            return None
