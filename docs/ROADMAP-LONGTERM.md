# Lyra — Long-term Architecture Roadmap

> Living document. Covers the full evolution from single-process prototype to distributed multi-machine engine.
> Last updated: 2026-03-05

---

## Target Architecture (end state)

```
                         NATS Cluster
                    ┌─────────────────────┐
                    │  lyra.inbound.*     │
                    │  lyra.outbound.*    │
                    │  lyra.task.*        │
                    │  lyra.health.*      │
                    └──────┬──────────────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
    Machine 1 (Hub)   Machine 2 (AI)    Machine N (future)
    ┌─────────────┐  ┌──────────────┐  ┌──────────────┐
    │ NATS client │  │ NATS client  │  │ NATS client  │
    │             │  │              │  │              │
    │ Hub core    │  │ LLM worker   │  │ Worker       │
    │ Adapters    │  │ (Ollama)     │  │ (any role)   │
    │ Memory DB   │  │              │  │              │
    │ TTS         │  │ SLM workers  │  │              │
    │ Embeddings  │  │ (Phase 3)    │  │              │
    │ Scheduler   │  │              │  │              │
    └─────────────┘  └──────────────┘  └──────────────┘
```

### Subject hierarchy

| Subject | Direction | Purpose |
|---------|-----------|---------|
| `lyra.inbound.{channel}` | Adapter -> Hub | Incoming messages from channels |
| `lyra.outbound.{channel}` | Hub -> Adapter | Responses routed back to channels |
| `lyra.task.llm` | Hub -> AI worker | LLM inference requests |
| `lyra.task.llm.reply` | AI worker -> Hub | LLM inference responses |
| `lyra.task.tts` | Any -> TTS worker | Text-to-speech requests |
| `lyra.task.embed` | Any -> Embed worker | Embedding requests |
| `lyra.task.slm.{role}` | Hub -> SLM worker | Atomic SLM requests (Phase 3) |
| `lyra.health.ping` | Any -> Any | Heartbeat / liveness |
| `lyra.health.status` | Worker -> Hub | Worker status reports |
| `lyra.event.*` | Pub/sub broadcast | System events (no queue group) |

### Queue groups

All task consumers use **queue groups** to ensure exactly-once delivery:
- Queue group `llm-workers` on `lyra.task.llm` — if 2 machines run LLM workers, each request goes to one only
- Queue group `hub` on `lyra.inbound.*` — if hub runs on 2 machines (HA), each message processed once
- Queue group `tts-workers` on `lyra.task.tts`

This is the answer to "what happens if I run Lyra on 2 machines" — NATS queue groups handle deduplication natively.

---

## Evolution phases

### Phase 1 — Single process (current)

```
Adapters -> asyncio.Queue(100) -> Hub -> Cloud LLM (Anthropic API)
```

- All in one Python process on Machine 1
- `asyncio.Queue` as the internal bus
- Cloud LLM only (Anthropic)
- Memory levels 0 + 3
- **Status**: in progress

### Phase 2 — NATS introduction + Machine 2

**Goal**: Replace `asyncio.Queue` with NATS. Enable Machine 2 as a worker.

```
Machine 1                          Machine 2
┌──────────────┐                  ┌──────────────┐
│ Adapters     │                  │              │
│   │          │                  │ LLM worker   │
│   ▼          │   NATS (M1)     │ (Ollama)     │
│ Hub core ◄───┼──────────────►──┤              │
│   │          │                  │              │
│   ▼          │                  └──────────────┘
│ Memory DB    │
│ TTS          │
│ Embeddings   │
└──────────────┘
```

#### Steps

| # | Action | Detail |
|---|--------|--------|
| 2.1 | Install NATS server on Machine 1 | Single node, no cluster. `nats-server` is a single ~20MB binary. |
| 2.2 | Add `nats-py` to Lyra dependencies | Async NATS client for Python. |
| 2.3 | Create `NatsBus` adapter | Same interface as `asyncio.Queue`, backed by NATS publish/subscribe. Hub code unchanged. |
| 2.4 | Migrate adapters to publish on `lyra.inbound.{channel}` | Adapters publish instead of `bus.put()`. |
| 2.5 | Hub subscribes to `lyra.inbound.*` with queue group `hub` | Hub consumes from NATS instead of `bus.get()`. |
| 2.6 | LLM worker on Machine 2 | Subscribes to `lyra.task.llm` (queue group `llm-workers`). Runs Ollama, returns response on `lyra.task.llm.reply`. |
| 2.7 | Health check | Hub publishes `lyra.health.ping`, workers reply. Dashboard endpoint on FastAPI. |
| 2.8 | Fallback logic | If Machine 2 doesn't reply within timeout, Hub falls back to cloud LLM (Anthropic). |

#### Key decisions

- **NATS server runs on Machine 1** — it's the 24/7 machine. Single node is fine for personal use.
- **No JetStream yet** — plain NATS pub/sub is sufficient. JetStream (persistence, replay) added only if needed later.
- **Backpressure preserved** — NATS has built-in slow consumer detection. The `NatsBus` adapter translates this to the same backpressure semantics as the current queue.
- **`asyncio.Queue` kept as fallback** — if NATS is down, Hub can fall back to in-process queue (degraded mode, no Machine 2).

#### Bus abstraction

```python
# src/lyra/core/bus.py
from abc import ABC, abstractmethod

class Bus(ABC):
    @abstractmethod
    async def publish(self, subject: str, data: bytes) -> None: ...

    @abstractmethod
    async def subscribe(self, subject: str, queue: str, handler: Callable) -> None: ...

class LocalBus(Bus):
    """Current asyncio.Queue — single process."""

class NatsBus(Bus):
    """NATS-backed bus — distributed."""
```

The Hub depends on `Bus`, not on the concrete implementation. Switching is a config change.

### Phase 3 — Atomic SLMs + distributed workers

**Goal**: Offload routing, NER, planning to small models. Workers can run on either machine.

```
Machine 1                                    Machine 2
┌──────────────────────┐                    ┌──────────────────────┐
│ NATS server          │                    │                      │
│                      │                    │ LLM worker (14B+)    │
│ Hub core             │◄──── NATS ────────►│ Router SLM (1-3B)    │
│ Adapters             │                    │ Planner SLM (3-7B)   │
│ Memory DB            │                    │ NER SLM (3B)         │
│ TTS worker           │                    │                      │
│ Embed worker         │                    └──────────────────────┘
└──────────────────────┘
```

| # | Action | Detail |
|---|--------|--------|
| 3.1 | Router SLM worker | Subscribes to `lyra.task.slm.router`. Classifies intent, returns routing decision. |
| 3.2 | Planner SLM worker | Subscribes to `lyra.task.slm.planner`. Selects skills, builds execution plan. |
| 3.3 | NER SLM worker | Subscribes to `lyra.task.slm.ner`. Extracts entities for memory update. |
| 3.4 | CognitiveFrame protocol | SLMs exchange `CognitiveFrame` structs over NATS (msgpack serialized). |
| 3.5 | Cognitive pipeline | Hub orchestrates: inbound -> router -> planner -> skills/LLM -> NER -> memory. Each step is a NATS request/reply. |
| 3.6 | Worker placement | SLMs run on Machine 2 (16GB VRAM). If VRAM allows, some can run on Machine 1. NATS doesn't care where they are. |

#### Cognitive flow over NATS

```
Adapter                Hub              Router SLM        Planner SLM         LLM Worker
   │                    │                   │                  │                   │
   │─ lyra.inbound.tg ─►│                   │                  │                   │
   │                    │── lyra.task.slm   ─►                  │                   │
   │                    │     .router        │                  │                   │
   │                    │◄─ CognitiveFrame ──│                  │                   │
   │                    │                    │                  │                   │
   │                    │── lyra.task.slm.planner ─────────────►│                   │
   │                    │◄─ CognitiveFrame ────────────────────│                   │
   │                    │                                       │                   │
   │                    │── lyra.task.llm ──────────────────────────────────────────►
   │                    │◄─ response ──────────────────────────────────────────────│
   │                    │                                                           │
   │◄─ lyra.outbound ──│
```

### Phase 4 — Resilience + scaling

**Goal**: High availability, persistence, observability.

| # | Action | Detail |
|---|--------|--------|
| 4.1 | JetStream | Enable NATS JetStream for persistent streams. Messages survive NATS restarts. Replay missed messages after worker crash. |
| 4.2 | NATS cluster (3 nodes) | If a third machine is added: 3-node NATS cluster for HA. Not needed with 2 machines. |
| 4.3 | Worker auto-scaling | Hub monitors queue depth on each subject. If `lyra.task.llm` backs up, alert or spin up another worker. |
| 4.4 | Distributed memory | Memory queries over NATS (`lyra.task.memory.query` / `lyra.task.memory.store`). Any worker can read/write memory without direct DB access. |
| 4.5 | Observability | NATS built-in monitoring + Prometheus exporter. Grafana dashboard for message rates, latencies, worker health. |
| 4.6 | Auth | NATS nkey/JWT authentication. Workers authenticate to the NATS server. No open bus on the network. |

### Phase 5 — Multi-agent orchestration

**Goal**: Multiple agents coordinate via NATS, not just via shared Hub state.

| # | Action | Detail |
|---|--------|--------|
| 5.1 | Agent-to-agent messaging | Agents publish/subscribe on `lyra.agent.{name}.*`. One agent can delegate tasks to another. |
| 5.2 | Workflow engine | DAG-based workflows over NATS. Hub publishes a workflow, workers execute steps, results flow back. |
| 5.3 | External integrations | Webhook receiver publishes to `lyra.inbound.webhook`. Calendar, email, CI/CD events enter the same bus. |
| 5.4 | Plugin marketplace | Third-party skills connect as NATS workers. Sandboxed: they only see their subscribed subjects. |

---

## Why NATS

| Criteria | NATS | Redis Pub/Sub | RabbitMQ | Kafka |
|----------|------|---------------|----------|-------|
| Binary size | ~20MB | ~8MB | ~150MB+ | ~500MB+ |
| Latency | <1ms | <1ms | ~5ms | ~10ms |
| Complexity | Minimal | Low | High | Very high |
| Queue groups | Native | Manual | Native | Consumer groups |
| Persistence | JetStream (opt-in) | Streams | Native | Native |
| Auth | nkey/JWT | Password/ACL | SASL | SASL/SSL |
| Clustering | Built-in | Sentinel/Cluster | Built-in | ZooKeeper/KRaft |
| Ops overhead | Near zero | Low | Medium | High |
| Python client | `nats-py` (async) | `redis` (async) | `aio-pika` | `aiokafka` |

**NATS wins for Lyra because**: near-zero ops, sub-millisecond latency, native queue groups, optional persistence via JetStream, single binary, designed for exactly this kind of distributed messaging.

---

## Timeline (indicative)

```
Phase 1 — Single process (NOW)
├── Hub + adapters + memory + cloud LLM
└── Target: stable, tested, Discord + Telegram working

Phase 2 — NATS + Machine 2 (after Phase 1 stable)
├── NATS server on Machine 1
├── Bus abstraction (LocalBus / NatsBus)
├── LLM worker on Machine 2
├── Health checks + fallback
└── Target: 2 machines coordinated, no duplicate messages

Phase 3 — Atomic SLMs (after Phase 2 + VRAM measured)
├── Router, Planner, NER as NATS workers
├── CognitiveFrame protocol
├── Cognitive pipeline orchestration
└── Target: 80-90% of messages skip the heavy LLM

Phase 4 — Resilience (when scale demands it)
├── JetStream persistence
├── Monitoring + alerting
├── Auth
└── Target: production-grade reliability

Phase 5 — Multi-agent (long-term vision)
├── Agent-to-agent coordination
├── Workflow engine
├── External integrations
└── Target: autonomous multi-agent system
```

No dates on Phases 3-5. They unlock based on prerequisites, not calendar.

---

## Migration strategy: asyncio.Queue -> NATS

The migration is **non-breaking** thanks to the `Bus` abstraction:

1. **Today**: Hub uses `asyncio.Queue` directly
2. **Step 1**: Extract `Bus` interface, wrap current queue as `LocalBus`
3. **Step 2**: Implement `NatsBus` behind the same interface
4. **Step 3**: Config toggle: `bus: local | nats` in settings
5. **Step 4**: Run NATS in parallel with local bus during testing
6. **Step 5**: Switch to NATS as default, keep local as fallback

Zero downtime. Zero rewrite. The Hub never knows which bus it's using.

---

## NATS deployment on Machine 1

```bash
# Install (one-time)
curl -fsSL https://get.nats.io | bash

# Or via apt
sudo apt install nats-server

# Config: /etc/nats/nats.conf
listen: 0.0.0.0:4222
max_payload: 1MB

# Optional: allow Machine 2
authorization {
  users = [
    { user: "lyra", password: "$NATS_PASSWORD" }
  ]
}

# Run via systemd
sudo systemctl enable nats-server
sudo systemctl start nats-server
```

Resource usage: ~10MB RAM, negligible CPU. Runs 24/7 alongside Lyra on Machine 1.
