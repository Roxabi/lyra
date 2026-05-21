"""Bot credential loader — reads tokens from /run/secrets/ Podman secrets."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from lyra.errors import MissingCredentialsError

log = logging.getLogger(__name__)


def load_bot_token(platform: str, bot_id: str) -> tuple[str, str | None]:
    """Read a bot's token and optional webhook secret from /run/secrets/.

    The base directory is overridable via LYRA_RUN_SECRETS_DIR (used by tests
    and local development). Defaults to /run/secrets in production containers.

    Raises MissingCredentialsError when the token file is absent — the message
    points the operator at `lyra bot secret install`.
    """
    base = Path(os.environ.get("LYRA_RUN_SECRETS_DIR", "/run/secrets"))
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
