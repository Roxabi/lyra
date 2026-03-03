# Challenge #6 — Crypto: Polymarket, Funding Rate & DeFi

> Challenge document based on knowledge base data.
> Last updated: 2026-03-02

---

## Our current plan

3 crypto tracks identified (all P3 or frozen):

**Priority 1**: Funding rate arbitrage
- Long spot position + short perpetual (or vice versa)
- Captures funding every 8h without directional exposure
- Paper trading 2-3 weeks minimum before real capital

**Polymarket + local LLM** (P3)
- LLM analyzes probabilities vs market price
- Exploits mispricings on recent events

**Passive yield DeFi**: USDC/USDT pools 5-15% APY

---

## What the knowledge base brings

### 1. Skills for AI agents on news + financial markets

**Source**: [@tom_doerr, Twitter](https://x.com/tom_doerr/status/2027848638571987326)

Agent skills for analyzing news and making financial market forecasts. Confirms that the "agent + Polymarket" niche is active in the community.

### 2. 2-level memory on a Polymarket agent — real field report

**Source**: [@lunarresearcher, Twitter](https://x.com/lunarresearcher/status/2028122076616200233)

This is not just a token optimization — it is a **Polymarket agent in production**. Someone is doing exactly what we planned (Polymarket + LLM) and publishing their results.

**What we learn**:
- The agent runs in prod, so the thesis is validated
- Costs can become significant at scale ($73/day without optimization)
- 2-level memory is the first optimization lever (not the model)

### 3. Karpathy — CLIs are ideal for agents

**Source**: [@karpathy, Twitter](https://x.com/karpathy/status/2026360908398862478)

> "CLIs are perfect for AI agents."

He demonstrates Claude building a terminal Polymarket dashboard in ~3 minutes via CLI.

**What this means for our agent**: a first Polymarket POC can be a simple CLI that Lyra uses as a tool. No need for a complex architecture to validate the thesis.

---

## What this calls into question

### 1. Funding rate arbitrage remains the riskiest

Our plan places funding rate at "Priority 1" but our ROADMAP.md explicitly freezes it:

> "Full-time subject, real capital, dedicated infrastructure. Not compatible with a solo launch."

The knowledge base data does not change this assessment. The 2-level memory on Polymarket is encouraging — but it is Polymarket (limited capital, discrete events), not perpetual trading (continuous capital, liquidation risk).

**Recommendation**: maintain the freeze on funding rate. Polymarket is the right first crypto project.

### 2. Polymarket before Machine 2?

Our roadmap places Polymarket at P3 (requires validated Machine 2). But the field report shows that a Polymarket agent can run with the cloud Anthropic API — no local LLM needed to get started.

**Possible revision**: Polymarket POC with cloud API at P2, migration to local LLM if costs explode (as in the field report).

### 3. Passive yield DeFi is the least risky

Stable USDC/USDT pools on Aave/Curve/Morpho. No directional risk, minimal smart contract risk on mature protocols.

**What we do not have in the knowledge base**: 2026 yield data on these pools. Must be checked manually.

---

## Revised Crypto Roadmap

### "Immediate" phase (without Machine 2)
- Polymarket agent POC with Anthropic API + 2-level memory (inspired by the field report)
- Test budget: 50-100 EUR on Polymarket to validate the edge before scaling

### Phase P2 (with Machine 2)
- Migrate the Polymarket agent to local LLM (reduce costs if volume grows)
- Passive yield DeFi: deploy an allocation on Aave USDC/USDT

### Frozen (do not touch)
- Funding rate arbitrage: too risky, too much capital, too much infrastructure
- MEV: out of reach
- CEX/DEX arbitrage: critical latency, high barrier to entry

---

## Verdict

Polymarket is the best entry point for crypto: limited capital, discrete events, local LLM not required to get started. The community validates the thesis (Polymarket agents in prod). Moving this POC from P3 to P2 is justified by the available data. Funding rate remains frozen.
