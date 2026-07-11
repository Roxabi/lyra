"""Docker-compose NATS integration tier (``nats_integration`` marker).

Layout (#2288):
- ``tests/nats/`` — local ``nats-server`` subprocess suites (``subprocess_nats``)
- ``tests/nats/integration/`` — docker compose NATS; CI ``integration`` job only

New docker-NATS suites MUST live here and declare
``pytestmark = pytest.mark.nats_integration`` at module scope.
"""
