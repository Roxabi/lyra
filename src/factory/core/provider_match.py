"""Duck-type ProviderError matching without importing factory.errors.

Keeps agent_cmd → core → … paths free of the factory.errors floating module
(peer-isolation contract in .importlinter).
"""


def is_provider_error(exc: BaseException) -> bool:
    return (
        type(exc).__name__ == "ProviderError"
        and type(exc).__module__ == "factory.errors"
    )