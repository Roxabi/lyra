
╔══╗─────────▶╔╗ ╔╗      ╔╗◀───┐
╚╣╠╝◀─────┐  ╔╝╚╗║║────▶╔╝╚╗   │
 ║║   ╔══╦══╦╩╗╔╝║║  ╔╦═╩╗╔╝╔═╦══╗
 ║║╔══╣╔╗║╔╗║╔╣║ ║║ ╔╬╣╔╗║║ ║│║╔═╝
╔╣╠╣║║║╚╝║╚╝║║║╚╗║╚═╝║║║║║╚╗║═╣║
╚══╩╩╩╣╔═╩══╩╝╚═╝╚═══╩╩╝╚╩═╩╩═╩╝
  └──▶║║                    ▲
      ╚╝────────────────────┘


---------
Contracts
---------

Analyzed 951 files, 6354 dependencies.
--------------------------------------

Clean architecture layers (transport ← streaming ← core ← llm/nats ←
infrastructure ← adapters ← bootstrap) KEPT
core/stores protocols must not import SQLite drivers KEPT
Commands must not import Infrastructure directly KEPT
Agents must not import Composition Root KEPT
Shared floating modules must not import each other (peer isolation) KEPT
bootstrap/types.py must not import from bootstrap subpackages (neutral shared
module invariant) KEPT
Shared floating modules must not import bootstrap, adapters, or infrastructure KEPT
adapters must not re-introduce the legacy streaming-emitter shim (#1279 T28) KEPT
Adapters must not import BotStore directly (use BotStoreProtocol instead) KEPT
inbound stages must not import adapters (stage-axis invariant, ADR-073 / #1287) KEPT
Production code must not import tests.fakes KEPT

Contracts: 11 kept, 0 broken.
