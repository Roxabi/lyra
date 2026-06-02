"""TurnWriter — subscriber-writer for L1 turn persistence.

Authorised by ADR-075. Single cross-platform subscriber consuming
`lyra.turns.write` from JetStream stream LYRA_TURNS via durable
consumer turn-writer-v1. Writes via TurnStore's private mutators.
"""

from factory.infrastructure.turn_writer.writer import TurnWriter

__all__ = ["TurnWriter"]
