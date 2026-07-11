"""In-process / mock integration suites under ``tests/integration/``.

Docker-compose NATS suites live under ``tests/nats/integration/`` (#2288)
and use the ``nats_integration`` marker (CI ``integration`` job).

Unmarked modules here run in the bulk ``tests`` job
(``-m "not nats_integration"``).
"""
