"""Bot credential loader — reads tokens from /run/secrets/ Podman secrets."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from factory.errors import MissingCredentialsError

log = logging.getLogger(__name__)


_PROD_SECRETS_DIR = Path("/run/secrets")


def _is_prod_env() -> bool:
    """Detect production runtime: container or explicit prod flag."""
    in_container = Path("/run/.containerenv").exists()
    explicit_prod = os.environ.get("FACTORY_ENV") == "prod"
    return bool(explicit_prod or in_container)


def load_bot_token(platform: str, bot_id: str) -> tuple[str, str | None]:
    """Read a bot's token and optional webhook secret from /run/secrets/.

    The base directory is overridable via FACTORY_RUN_SECRETS_DIR (used by tests
    and local development). Defaults to /run/secrets in production containers.

    In production (container or FACTORY_ENV=prod) the override is ignored as a
    defense-in-depth measure — an attacker with env-write access cannot redirect
    token reads to an arbitrary path.

    Raises MissingCredentialsError when the token file is absent — the message
    points the operator at `lyra bot secret install`.
    """
    override = os.environ.get("FACTORY_RUN_SECRETS_DIR")
    if override and _is_prod_env():
        log.warning(
            "FACTORY_RUN_SECRETS_DIR is set to %s but ignored in production "
            "(container or FACTORY_ENV=prod). Using %s.",
            override,
            _PROD_SECRETS_DIR,
        )
        base = _PROD_SECRETS_DIR
    else:
        base = Path(override) if override else _PROD_SECRETS_DIR
    tok_path = base / f"bot_token-{bot_id}"
    try:
        raw_token = tok_path.read_text()
    except FileNotFoundError as exc:
        raise MissingCredentialsError(
            platform,
            bot_id,
            hint=(
                f"missing token at {tok_path} — provision via "
                f"`lyra bot secret install {platform} {bot_id}`"
            ),
        ) from exc
    token = raw_token.strip()
    if token != raw_token:
        log.warning(
            "bot_token-%s contained leading/trailing whitespace — stripped before use",
            bot_id,
        )
    wh_path = base / f"bot_webhook-{bot_id}"
    try:
        raw_webhook: str | None = wh_path.read_text()
    except FileNotFoundError:
        raw_webhook = None
    webhook = raw_webhook.strip() if raw_webhook is not None else None
    return (token, webhook)
