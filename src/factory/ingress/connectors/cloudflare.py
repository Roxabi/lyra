"""Cloudflare Notifications connector (Family B)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from factory.ingress.connectors.base import validate_factory_tenant
from factory.ingress.normalize import (
    build_lyra_event,
    cloudflare_event_kind,
    cloudflare_event_level,
)
from factory.ingress.payload import summarize_cloudflare_payload
from factory.ingress.ports import (
    InstallationRegistry,
    SecretResolver,
    VerificationError,
)
from factory.ingress.verify import verify_cloudflare_auth
from roxabi_contracts.event import LyraEvent


class CloudflareConnector:
    name = "cloudflare"
    family: Literal["centralized_app", "per_account"] = "per_account"

    def verify(
        self,
        headers: Mapping[str, str],
        body: bytes,
        secrets: SecretResolver,
        *,
        path_tenant: str | None,
    ) -> None:
        if not path_tenant:
            raise VerificationError("invalid auth")
        tenant = validate_factory_tenant(path_tenant)
        secret = secrets.get_tenant_secret(self.name, tenant)
        if not secret:
            raise VerificationError("invalid auth")
        auth = headers.get("cf-webhook-auth")
        if not verify_cloudflare_auth(auth, secret):
            raise VerificationError("invalid auth")

    def parse_external_id(
        self,
        headers: Mapping[str, str],
        body: dict[str, Any],
        *,
        path_tenant: str | None,
    ) -> str | None:
        account_id = body.get("account_id")
        if account_id is not None:
            return str(account_id)
        return None

    async def apply_lifecycle(
        self,
        headers: Mapping[str, str],
        body: dict[str, Any],
        registry: InstallationRegistry,
    ) -> None:
        return

    def normalize(
        self,
        headers: Mapping[str, str],
        body: dict[str, Any],
        *,
        tenant: str,
    ) -> LyraEvent:
        kind = cloudflare_event_kind(body)
        return build_lyra_event(
            service=self.name,
            kind=kind,
            level=cloudflare_event_level(kind),
            message=f"cloudflare {kind}",
            payload=summarize_cloudflare_payload(body),
            tenant=tenant,
        )