"""TurnWriter — subscriber-writer for L1 turn persistence.

Authorised by ADR-075. Single cross-platform subscriber consuming
`factory.turns.write` from JetStream stream FACTORY_TURNS via durable
consumer turn-writer-v1. Writes via TurnStore's private mutators.
"""

from factory.infrastructure.turn_writer.writer import TurnWriter

__all__ = ["TurnWriter"]
