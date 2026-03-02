# Agents — How to define an agent

An agent is a stateless singleton — an immutable config (system prompt, permissions, memory namespace). All mutable state lives in the Pool. Multiple users can be served by the same agent instance simultaneously without any race conditions.

<!-- TODO: write this doc after the first real agent (lyra) is implemented -->

## Structure

```python
@dataclass(frozen=True)
class Agent:
    name: str
    system_prompt: str
    memory_namespace: str
    permissions: tuple[str, ...]

    async def process(self, msg: Message, pool: Pool) -> Response:
        ...
```

<!-- TODO: document:
  - how to subclass Agent and implement process()
  - how to access pool.history for conversation context
  - how to call the LLM (cloud via Anthropic API, local via Machine 2)
  - how to invoke skills from process()
  - how to write to semantic memory
  - how to register the agent with the hub via bindings
-->

## Memory access

<!-- TODO: document the memory namespace isolation per agent in SQLite -->

## Permissions

<!-- TODO: document the skill permission system — what each permission grants -->
