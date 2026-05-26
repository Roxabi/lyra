import re
from dataclasses import dataclass

# `platform` and `bot_id` flow into NATS subjects (`lyra.typing.{platform}.{bot_id}`
# and others). NATS specials `.`, `*`, `>` would create wildcard-matching subjects;
# the length cap bounds subject size and the error-message log line.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,48}$")

# `trace_id` flows to wire payloads (`TypingEvent`/`ContractEnvelope`) and logs.
# Wider charset (hex/UUID conventions) but still bounded against log pollution
# and CRLF injection in structured-log sinks. Subsumes the #1392 non-empty
# guard (the {1,128} bound + tight charset implies length ≥ 1, no whitespace).
_TRACE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@dataclass(frozen=True, slots=True)
class WorkScope:
    """Scope identifier for a single conversation turn.

    `platform` and `bot_id` are interpolated directly into NATS subjects;
    both MUST match `^[A-Za-z0-9_-]{1,48}$` (no NATS specials, bounded length)
    so a caller can never publish to a wildcard-matching subject.

    `trace_id` is not injected into subjects but flows to wire payloads and
    structured logs; it MUST match `^[A-Za-z0-9_-]{1,128}$` — supersedes the
    #1392 non-empty guard with a tighter charset + length bound.

    `scope_id` is `int` and is not interpolated into any subject — not
    validated here.
    """

    platform: str
    bot_id: str
    scope_id: int
    trace_id: str

    def __post_init__(self) -> None:
        for field, value, pattern in (
            ("platform", self.platform, _TOKEN_RE),
            ("bot_id", self.bot_id, _TOKEN_RE),
            ("trace_id", self.trace_id, _TRACE_ID_RE),
        ):
            if not pattern.fullmatch(value):
                raise ValueError(
                    f"WorkScope.{field} must match {pattern.pattern}; got {value!r}"
                )
