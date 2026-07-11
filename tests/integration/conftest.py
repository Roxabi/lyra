"""Integration test tier markers.

CI partition (Slice 4):
- ``nats_integration`` — docker compose NATS; runs in the ``integration`` job only.
- Unmarked modules — in-process mocks; run in the bulk ``tests`` job
  (``-m "not nats_integration"``).

New docker-NATS suites MUST declare ``pytestmark = pytest.mark.nats_integration``
at module scope. See ``test_voice_routing.py``.
"""
