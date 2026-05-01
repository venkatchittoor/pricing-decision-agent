# 💰 Pricing Decision Agent

> *Autonomous AI agent that analyzes revenue, return, and pricing signals across product categories, reasons about opportunities using Claude API, and self-determines when to act — auto-applying HIGH confidence price adjustments while escalating uncertain decisions for human review.*

---

## Business Impact

> Manual pricing review cycles introduce delays, inconsistency, and analyst bottlenecks. This agent eliminates the bottleneck for high-confidence decisions — applying price changes instantly on HIGH confidence signals while routing MEDIUM/LOW confidence cases to human review with full reasoning attached. Autonomous where safe, deferential where it matters.

Pricing is one of the highest-leverage decisions in e-commerce — and one of the hardest to get right consistently.

Most businesses either:
- **Under-react** — review pricing quarterly in spreadsheets, missing signals hiding in daily transaction data
- **Over-automate** — set rigid rules ("if return rate > 15%, discount 10%") that fire incorrectly because they look at one signal in isolation

The gap between these two extremes is judgment — the ability to look at multiple signals simultaneously, weigh them in context, and decide both what to do and how confident to be about it.

This agent fills that gap.

---

## How This Differs From a Monitoring Agent

This portfolio includes two agentic projects. Understanding the difference between them is important:

| | `data-incident-agent` | `pricing-decision-agent` |
|---|---|---|
| **Type** | Monitoring agent | Decisioning agent |
| **Question it answers** | *"What is wrong?"* | *"What should we do about it?"* |
| **Output** | Incident report | Autonomous action + human review queue |
| **Autonomy** | Observes and reports | Observes, decides, and **acts** |
| **Risk profile** | Low — reports never break anything | Higher — actions change real data |
| **Confidence gating** | Not needed | Essential — agent must know when NOT to act |

Think of the monitoring agent as a **doctor who diagnoses**.
Think of the decisioning agent as a **surgeon who operates** — but only when they are confident enough to pick up the scalpel.

The critical new capability in a decisioning agent is not the action itself — it is the **judgment about when to act autonomously vs when to defer to a human**. An agent that acts on everything is reckless. An agent that only recommends is timid. A production-grade decisioning agent reasons about its own certainty and gates action on confidence.

---

## The Approach: A Portfolio Manager, Not a Vending Machine

> A rules-based pricing engine is like a **vending machine** — insert signal, receive output. High return rate → apply discount. Every time, without context.
>
> This agent is like a **portfolio manager** — it looks at the full picture, weighs competing signals, forms a view, decides how confident it is, and only commits capital when the conviction is high enough.

The key distinction: a high return rate means different things depending on context:
- High returns + high avg order value = **pricing resistance** — customers buying then regretting
- High returns + low avg order value = **quality issue** — price is not the problem
- High returns + low base price = **investigate first** — the signal is contradictory

A vending machine fires the same action regardless. This agent reads the context.

---

## Architecture: Eyes → Brain → Hands

```
┌─────────────────────────────────────────────────────────┐
│                     run_agent.py                        │
│                  (single entrypoint)                    │
└──────────┬──────────────────┬──────────────────┬────────┘
           │                  │                  │
           ▼                  ▼                  ▼
  collect_signals.py       agent.py        decision_writer.py
       [Eyes]               [Brain]            [Hands]

  "What do the           "What should         "Apply HIGH confidence
   pricing signals        we do about it?      decisions. Queue the
   look like?"            And how sure          rest for human review."
                          are we?"
```

| Module | Role | What it does |
|---|---|---|
| `collect_signals.py` | 👁️ Eyes | Pulls revenue, return, and price signals from 3 Gold tables, joins by category |
| `agent.py` | 🧠 Brain | 3-turn Claude API reasoning loop — analyze, decide, act |
| `decision_writer.py` | 🤝 Hands | Persists decisions to JSON file + 2 Delta tables, flags auto-applied vs human review |
| `run_agent.py` | 🎯 Entrypoint | Single CLI command orchestrating the full pipeline |

---

## The Three Signal Sources

No single metric tells the full story. The agent reads three lenses simultaneously:

| Signal Source | Table | What it reveals |
|---|---|---|
| Revenue signals | `gold_revenue_by_category` | Total revenue, units sold, avg order value, order count |
| Return signals | `gold_return_analysis` | Return rate, total returns, revenue lost to returns |
| Price context | `bronze_products` | Avg, min, max base price per category, product count |

The multi-signal join is what enables genuine reasoning. High return rate means something different at $500 AOV vs $17 AOV — and the agent knows that.

---

## The Decisioning Loop (3 Turns)

```
Turn 1 — Analyze
  Feed all 7 category signals to Claude (revenue + returns + price context)
  Claude identifies pricing opportunities and concerns
  Reasons about WHAT each signal combination means
  No thresholds. Multi-signal judgment.
        ↓
Turn 2 — Decide
  For each flagged category, Claude recommends a specific adjustment
  Assigns confidence: HIGH / MEDIUM / LOW
  HIGH = signals clearly converge, modest adjustment, unambiguous reasoning
  MEDIUM = signals mixed or adjustment aggressive, needs human validation
  LOW = signals contradictory, investigate before acting
        ↓
Turn 3 — Act
  HIGH confidence → auto_apply = True (agent acts autonomously)
  MEDIUM / LOW → queued for human review with explicit reason_for_review
  All decisions logged with full reasoning audit trail
```

The confidence-gating in Turn 3 is the defining characteristic of a production decisioning agent. The agent is not just making recommendations — it is reasoning about the quality of its own reasoning and deciding when that quality is high enough to act without supervision.

---

## Sample Output

```
============================================================
  💰 PRICING DECISION AGENT
  2026-04-22 16:35:10 UTC
============================================================

  📊 Categories analyzed : 7
  🚩 Categories flagged  : 3
  🤖 Auto-applied        : 1 HIGH confidence decisions
  👤 Human review queue  : 2 decisions
============================================================

  MARKET SUMMARY:
  Electronics, Sports, Clothing, and Toys show healthy pricing equilibrium.
  Three categories need attention: Home & Kitchen appears overpriced,
  Beauty shows underpricing opportunity, Books has quality concerns.

  🤖 AUTO-APPLIED (HIGH confidence):

  ▼ Home & Kitchen
    Adjustment     : -12% → new price $166.89
    Expected impact: Reduce return rate from 16.03% to ~13%, recover
                     $8,000-12,000 of the $32,123 currently lost to returns

  👤 HUMAN REVIEW QUEUE:

  🟡 Beauty [MEDIUM]
    Suggestion     : +18% → $47.05
    Reason         : Large increase on high-volume category needs competitive
                     validation and A/B testing before implementation

  🔴 Books [LOW]
    Suggestion     : No price change
    Reason         : High returns on low-priced items indicate quality or
                     fulfillment issues — pricing is not the problem here

  ACTION SUMMARY:
  Total potential impact: $15,000-20,000 from reduced Home & Kitchen returns
  plus $6,000-8,000 additional Beauty revenue if price increase succeeds.
```

**Notice what happened here:**
- Home & Kitchen: multiple signals pointed the same direction → HIGH confidence → autonomous action
- Beauty: strong opportunity but aggressive adjustment → MEDIUM → human validates first
- Books: contradictory signals (high returns at rock-bottom prices) → LOW → investigate quality, not price

Seven categories in. One action taken. Two escalated. One correctly identified as a non-pricing problem. Zero hardcoded rules.

---

## Decisions as Data

Every agent run writes to two Delta tables — because decisions are data, and data engineers store things as data.

Think of it like a hospital:
- `pricing_decisions` = the **admission record** — one row per agent run, executive summary
- `pricing_actions` = the **treatment log** — one row per category decision, full reasoning preserved

```sql
-- How many runs resulted in auto-applied decisions this month?
SELECT COUNT(*) FROM workspace.ecommerce.pricing_decisions
WHERE auto_apply_count > 0;

-- Full pricing history for Home & Kitchen
SELECT generated_at, adjustment_pct, confidence, reasoning
FROM workspace.ecommerce.pricing_actions
WHERE category = 'Home & Kitchen'
ORDER BY generated_at DESC;

-- Which categories keep landing in human review?
SELECT category, COUNT(*) as review_count, AVG(adjustment_pct) as avg_suggestion
FROM workspace.ecommerce.pricing_actions
WHERE auto_applied = FALSE
GROUP BY category ORDER BY review_count DESC;
```

Over time this becomes a queryable pricing intelligence layer — not just a log file.

---

## Setup

### Prerequisites
- Python 3.8+
- Databricks workspace with Unity Catalog
- Anthropic API key ([console.anthropic.com](https://console.anthropic.com))

### Installation

```bash
git clone https://github.com/YOUR_USERNAME/pricing-decision-agent.git
cd pricing-decision-agent
python3 -m venv venv
source venv/bin/activate
pip install databricks-sdk requests anthropic python-dotenv
```

### Configuration

Create a `.env` file in the project root:

```
DATABRICKS_HOST=https://your-workspace.azuredatabricks.net
DATABRICKS_TOKEN=your-personal-access-token
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/your-warehouse-id
ANTHROPIC_API_KEY=your-anthropic-api-key
```

> ⚠️ `.env` is in `.gitignore` — your secrets will never be committed.

---

## Usage

```bash
# Full run — collect signals, reason, write decisions
python run_agent.py

# Dry run — Eyes + Brain only, skip writing decisions
python run_agent.py --dry-run

# Output raw JSON
python run_agent.py --json
```

### Exit codes
| Code | Meaning |
|---|---|
| `0` | No auto-applied decisions |
| `2` | At least one decision was auto-applied |

Exit code 2 can trigger downstream notifications in a Databricks Job — same pattern as `data-incident-agent`.

---

## Why Not a Rules Engine?

A rules-based pricing engine is faster to build and easier to explain. So why an agent?

Because rules are brittle at the boundary cases — and boundary cases are where the most value and the most risk live.

Rules cannot distinguish between:
- A 16% return rate caused by overpricing vs poor product quality
- A low-revenue category that is underpriced vs simply low-demand
- A high-volume category with room to raise prices vs one where volume is price-sensitive

An agent reasons about these distinctions using all available signals. More importantly, it knows when it *cannot* distinguish between them — and in those cases, it defers to a human rather than firing a potentially wrong rule.

That combination of autonomous action and calibrated deference is what makes it production-grade.

---

## What Makes This Truly Autonomous

The auto-apply action alone does not make this agent autonomous. A simple script can write to a Delta table. That is just automation.

What makes this agent autonomous is the full loop — and specifically what happens at each step:

1. **It decided *what* to look at** — no one told it which categories to flag. It read all 7 and formed its own view based on the signal landscape.

2. **It reasoned about *why*** — it did not just detect high returns. It interpreted what high returns *meant* in the context of price point, AOV, and revenue pattern. Same metric, different meaning depending on context.

3. **It assessed its own confidence** — this is the part most people miss. The agent did not just decide *what* to do, it decided *how sure it was* about doing it. That meta-reasoning — thinking about the quality of its own thinking — is the defining characteristic of an autonomous agent.

4. **It acted selectively based on that confidence** — Home & Kitchen got auto-applied. Beauty got escalated. Books got flagged as a non-pricing problem entirely. Three different outcomes from three different reasoning paths.

A useful way to think about where this sits on the automation spectrum:

```
Script          Automation       Agent           Autonomous Agent
  │                 │              │                    │
Hardcoded       If/else         Reasons             Reasons AND
steps           rules           about what          reasons about
                                to do               its own certainty
                                                    before acting
```

Most AI demos live in the third column — they reason about what to do, but always produce the same type of output (a recommendation, a report, a summary). This project lives in the fourth column: the agent reasons about its own certainty and uses that to gate whether it acts autonomously or defers to a human.

That is the difference between an AI assistant and an AI agent.

---

## Portfolio Context

This project is part of a Databricks + Claude API data engineering portfolio:

| Repo | Agent Type | What it demonstrates |
|---|---|---|
| [`ecommerce-pipeline`](https://github.com/YOUR_USERNAME/ecommerce-pipeline) | — | Medallion Architecture, PySpark, DLT, streaming, NL assistant |
| [`data-incident-agent`](https://github.com/YOUR_USERNAME/data-incident-agent) | Monitoring agent | Observes, diagnoses, reports — never acts |
| [`pricing-decision-agent`](https://github.com/YOUR_USERNAME/pricing-decision-agent) | Decisioning agent | Observes, decides, acts — with confidence-gated autonomy |

Together these three repos demonstrate the full spectrum: from pipeline engineering → AI-powered monitoring → autonomous decisioning.

---

## Tech Stack

- **Databricks** — lakehouse platform, Delta tables, SQL warehouses, REST API
- **Claude API (Anthropic)** — LLM reasoning and confidence-scoring engine
- **Databricks SDK** — auto-credential detection, warehouse discovery
- **Python** — `requests`, `python-dotenv`, `databricks-sdk`

---

## Roadmap

- [ ] **v2 — Price update writeback:** Apply HIGH confidence decisions directly to a `pricing_overrides` Delta table consumed by the product catalog
- [ ] **v3 — A/B test tracker:** Log MEDIUM confidence decisions as A/B test candidates with a 30-day evaluation window
- [ ] **v4 — Scheduled Job:** Run weekly as a Databricks Job with email summary on auto-applied decisions

---

*Built by Venkat Chittoor in collaboration with Claude (Anthropic) — a demonstration that the engineers who thrive in the AI era are not those who know everything, but those who adapt fast, leverage the best tools available, and ship things that matter. The future belongs to those who adapt and adopt.*
