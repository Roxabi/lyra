"""GitHub App webhook connector (Family A)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from factory.ingress.normalize import (
    build_lyra_event,
    github_event_kind,
    github_event_level,
    github_event_message,
)
from factory.ingress.payload import summarize_github_payload
from factory.ingress.ports import (
    InstallationRegistry,
    SecretResolver,
    VerificationError,
)
from factory.ingress.verify import verify_github_signature
from roxabi_contracts.event import LyraEvent


class GitHubConnector:
    name = "github"
    family: Literal["centralized_app", "per_account"] = "centralized_app"

    def verify(
        self,
        headers: Mapping[str, str],
        body: bytes,
        secrets: SecretResolver,
        *,
        path_tenant: str | None,
    ) -> None:
        secret = secrets.get_connector_secret(self.name)
        signature = headers.get("X-Hub-Signature-256")
        if not verify_github_signature(body, signature, secret):
            raise VerificationError("invalid auth")

    def parse_external_id(
        self,
        headers: Mapping[str, str],
        body: dict[str, Any],
        *,
        path_tenant: str | None,
    ) -> str | None:
        installation = body.get("installation")
        if isinstance(installation, dict) and installation.get("id") is not None:
            return str(installation["id"])
        return None

    async def apply_lifecycle(
        self,
        headers: Mapping[str, str],
        body: dict[str, Any],
        registry: InstallationRegistry,
    ) -> None:
        if headers.get("X-GitHub-Event") != "installation":
            return
        action = body.get("action")
        installation = body.get("installation")
        if not isinstance(installation, dict) or installation.get("id") is None:
            return
        external_id = str(installation["id"])
        if action == "created":
            await registry.upsert_lifecycle(
                self.name, external_id, "default", enabled=True
            )
        elif action == "deleted":
            await registry.upsert_lifecycle(
                self.name, external_id, "default", enabled=False
            )

    def normalize(
        self,
        headers: Mapping[str, str],
        body: dict[str, Any],
        *,
        tenant: str,
    ) -> LyraEvent:
        event_name = headers.get("X-GitHub-Event", "unknown")
        delivery_id = headers.get("X-GitHub-Delivery", "")
        kind = github_event_kind(event_name, body)
        return build_lyra_event(
            service=self.name,
            kind=kind,
            level=github_event_level(kind, body),
            message=github_event_message(kind, body),
            payload=summarize_github_payload(body),
            trace_id=delivery_id or None,
            tenant=tenant,
        )