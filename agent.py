from dotenv import load_dotenv
load_dotenv()

"""
agent.py
--------
The BRAIN of the pricing-decision-agent.

Implements a 3-turn decisioning loop:
  Turn 1 — Analyze:  Feed all category signals to Claude. Identify pricing
                     opportunities and concerns across multiple signals.
  Turn 2 — Decide:   For each flagged category, Claude recommends a specific
                     price adjustment with a confidence score and explicit reasoning.
  Turn 3 — Act:      Agent auto-applies HIGH confidence decisions. MEDIUM/LOW
                     decisions are queued for human review.

The confidence-gating in Turn 3 is what makes this a DECISIONING agent:
  - Reckless agent: acts on everything
  - Timid agent: recommends everything, acts on nothing
  - Production agent: reasons about its own certainty and decides when to act

Unlike a rules-based pricing engine (if return_rate > 15% then discount 10%),
this agent weighs multiple signals simultaneously and forms a judgment.
High returns could mean overpriced OR poor quality — only multi-signal
reasoning can tell the difference.
"""

import os
import json
import requests
from datetime import datetime, timezone

from collect_signals import collect_all_signals

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
CLAUDE_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 2000

# Confidence threshold for autonomous action
AUTO_APPLY_THRESHOLD = "HIGH"


# ---------------------------------------------------------------------------
# Claude API helper
# ---------------------------------------------------------------------------

def _call_claude(messages: list, system: str) -> str:
    """Send a message list to Claude and return the text response."""
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": CLAUDE_MODEL,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": messages,
        },
    )
    response.raise_for_status()
    return response.json()["content"][0]["text"]


# ---------------------------------------------------------------------------
# Turn 1 — Analyze
# ---------------------------------------------------------------------------

def turn1_analyze(signals: dict) -> tuple:
    """
    Feed all category signals to Claude.
    Claude identifies pricing opportunities and concerns with reasoning.
    Returns Claude's analysis text and conversation history.
    """
    print("\n🔍 Turn 1 — Analyze: Feeding category signals to Claude...")

    system = """You are an expert pricing strategist and data analyst for an e-commerce business.

You will be given enriched signals for each product category including:
- Revenue metrics (total_revenue, avg_order_value, order_count, total_units)
- Return metrics (return_rate, total_returns, total_revenue_lost)
- Price context (avg_base_price, min_base_price, max_base_price, product_count)

Your job is to identify pricing opportunities and concerns. Think carefully:
- High return rate + high avg_order_value = likely overpriced (customers buying then regretting)
- High return rate + low avg_order_value = likely quality issue, not pricing
- Low revenue + high units = underpriced (selling volume but not value)
- Low return rate + healthy revenue = pricing sweet spot, consider modest increase
- High revenue_lost from returns = pricing adjustment could reduce return incentive

For each category that warrants attention, explain the multi-signal reasoning
behind your assessment. Be specific — reference actual numbers.

Respond in this JSON format only:
{
  "flagged_categories": [
    {
      "category": "category name",
      "opportunity_type": "REDUCE_PRICE | INCREASE_PRICE | INVESTIGATE_QUALITY",
      "reasoning": "specific multi-signal explanation referencing actual numbers",
      "priority": "HIGH | MEDIUM | LOW"
    }
  ],
  "healthy_categories": ["list of category names that look well-priced"],
  "market_summary": "one paragraph overview of overall pricing health across all categories"
}

Return JSON only — no preamble, no markdown fences."""

    signals_text = json.dumps(signals, indent=2)
    user_message = f"""Here are the current pricing signals across all product categories:

{signals_text}

Analyze these signals and identify categories that may benefit from a pricing adjustment.
Consider all three signal sources together — revenue, returns, and base prices."""

    messages = [{"role": "user", "content": user_message}]
    response_text = _call_claude(messages, system)
    messages.append({"role": "assistant", "content": response_text})

    print("   ✅ Claude completed pricing analysis.")
    return response_text, messages


# ---------------------------------------------------------------------------
# Turn 2 — Decide
# ---------------------------------------------------------------------------

def turn2_decide(flagged_categories: list, signals: dict, messages: list) -> tuple:
    """
    For each flagged category, Claude recommends a specific price adjustment
    with a confidence score and explicit reasoning.
    Returns Claude's decisions and updated conversation history.
    """
    print("\n🧠 Turn 2 — Decide: Generating price recommendations...")

    # Build a quick lookup for category signals
    signals_by_cat = {c["category"]: c for c in signals.get("categories", [])}

    # Enrich flagged categories with their raw signal data
    enriched_flags = []
    for flag in flagged_categories:
        cat = flag.get("category")
        enriched_flags.append({
            **flag,
            "current_signals": signals_by_cat.get(cat, {}),
        })

    user_message = f"""Based on your analysis, here are the flagged categories with their full signal data:

{json.dumps(enriched_flags, indent=2)}

For each flagged category, provide a specific pricing recommendation.

Important confidence scoring guidance:
- HIGH confidence: Multiple signals clearly point in the same direction,
  the adjustment is modest (5-15%), and the reasoning is unambiguous
- MEDIUM confidence: Signals are mixed or the recommended adjustment is larger,
  requires human validation before applying
- LOW confidence: Signals are contradictory or data is insufficient,
  recommend further investigation before any price change

Respond in this JSON format only:
{{
  "recommendations": [
    {{
      "category": "category name",
      "opportunity_type": "REDUCE_PRICE | INCREASE_PRICE | INVESTIGATE_QUALITY",
      "current_avg_price": <number>,
      "recommended_adjustment_pct": <number, negative for reduction e.g. -8>,
      "recommended_new_price": <number>,
      "confidence": "HIGH | MEDIUM | LOW",
      "reasoning": "detailed multi-signal justification",
      "risk_factors": "what could make this recommendation wrong",
      "auto_apply": <true if HIGH confidence, false otherwise>
    }}
  ]
}}

Return JSON only — no preamble, no markdown fences."""

    messages.append({"role": "user", "content": user_message})
    response_text = _call_claude(messages, system="You are an expert pricing strategist. Respond only in the JSON format requested.")
    messages.append({"role": "assistant", "content": response_text})

    print("   ✅ Claude generated price recommendations.")
    return response_text, messages


# ---------------------------------------------------------------------------
# Turn 3 — Act
# ---------------------------------------------------------------------------

def turn3_act(recommendations: list, messages: list) -> tuple:
    """
    Claude reviews all recommendations and produces the final action plan.
    HIGH confidence → auto-apply flag set to True
    MEDIUM/LOW → queued for human review
    Returns final action plan and updated conversation history.
    """
    print("\n⚡ Turn 3 — Act: Determining autonomous vs human-review actions...")

    auto_apply = [r for r in recommendations if r.get("confidence") == AUTO_APPLY_THRESHOLD]
    human_review = [r for r in recommendations if r.get("confidence") != AUTO_APPLY_THRESHOLD]

    user_message = f"""Here is the complete set of pricing recommendations:

AUTO-APPLY candidates (HIGH confidence):
{json.dumps(auto_apply, indent=2)}

HUMAN REVIEW queue (MEDIUM/LOW confidence):
{json.dumps(human_review, indent=2)}

Produce the final action plan. For each recommendation confirm:
1. Whether it should be auto-applied or queued for human review
2. The expected business impact if the recommendation is correct
3. Any sequencing considerations (e.g. adjust one category before another)

Respond in this JSON format only:
{{
  "auto_apply": [
    {{
      "category": "category name",
      "adjustment_pct": <number>,
      "new_price": <number>,
      "expected_impact": "specific expected business outcome",
      "confidence": "HIGH"
    }}
  ],
  "human_review": [
    {{
      "category": "category name",
      "adjustment_pct": <number>,
      "suggested_new_price": <number>,
      "confidence": "MEDIUM | LOW",
      "reason_for_review": "why human judgment is needed here"
    }}
  ],
  "action_summary": "one paragraph summary of the full action plan and expected outcomes"
}}

Return JSON only — no preamble, no markdown fences."""

    messages.append({"role": "user", "content": user_message})
    response_text = _call_claude(messages, system="You are an expert pricing strategist. Respond only in the JSON format requested.")
    messages.append({"role": "assistant", "content": response_text})

    print("   ✅ Claude finalized action plan.")
    return response_text, messages


# ---------------------------------------------------------------------------
# Main agentic loop
# ---------------------------------------------------------------------------

def run_agent() -> dict:
    """
    Orchestrates the full 3-turn pricing decisioning loop:
      Eyes (collect_signals) → Brain Turn 1 (analyze) →
      Brain Turn 2 (decide) → Brain Turn 3 (act)
    """
    print("=" * 60)
    print("🤖 PRICING DECISION AGENT — Starting decisioning loop")
    print("=" * 60)

    # ── Eyes: collect signals ──────────────────────────────────────
    signals = collect_all_signals()

    # ── Brain Turn 1: Analyze ──────────────────────────────────────
    turn1_response, messages = turn1_analyze(signals)

    try:
        clean = turn1_response.strip().replace("```json", "").replace("```", "")
        turn1_data = json.loads(clean)
    except json.JSONDecodeError:
        print("⚠️  Could not parse Turn 1 response. Aborting.")
        return {"error": "Turn 1 parse failure", "raw": turn1_response}

    flagged = turn1_data.get("flagged_categories", [])
    healthy = turn1_data.get("healthy_categories", [])
    summary = turn1_data.get("market_summary", "")

    print(f"\n   📌 Market summary: {summary}")
    print(f"   📌 Flagged categories: {len(flagged)}")
    print(f"   📌 Healthy categories: {healthy}")

    if not flagged:
        print("\n✅ All categories appear well-priced. No adjustments recommended.")
        return {
            "run_id": f"pricing-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "NO_ACTION",
            "market_summary": summary,
            "auto_apply": [],
            "human_review": [],
        }

    # ── Brain Turn 2: Decide ───────────────────────────────────────
    turn2_response, messages = turn2_decide(flagged, signals, messages)

    try:
        clean = turn2_response.strip().replace("```json", "").replace("```", "")
        turn2_data = json.loads(clean)
    except json.JSONDecodeError:
        print("⚠️  Could not parse Turn 2 response. Aborting.")
        return {"error": "Turn 2 parse failure", "raw": turn2_response}

    recommendations = turn2_data.get("recommendations", [])
    print(f"\n   📌 Recommendations generated: {len(recommendations)}")
    for r in recommendations:
        emoji = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}.get(r.get("confidence"), "⚪")
        print(f"   {emoji} {r.get('category')} → {r.get('recommended_adjustment_pct')}% [{r.get('confidence')}]")

    # ── Brain Turn 3: Act ──────────────────────────────────────────
    turn3_response, messages = turn3_act(recommendations, messages)

    try:
        clean = turn3_response.strip().replace("```json", "").replace("```", "")
        turn3_data = json.loads(clean)
    except json.JSONDecodeError:
        print("⚠️  Could not parse Turn 3 response. Aborting.")
        return {"error": "Turn 3 parse failure", "raw": turn3_response}

    # Build final report
    report = {
        "run_id": f"pricing-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "signals_collected_at": signals.get("collected_at"),
        "categories_analyzed": signals.get("category_count"),
        "categories_flagged": len(flagged),
        "market_summary": summary,
        "auto_apply": turn3_data.get("auto_apply", []),
        "human_review": turn3_data.get("human_review", []),
        "action_summary": turn3_data.get("action_summary", ""),
        "all_recommendations": recommendations,
    }

    auto_count = len(report["auto_apply"])
    review_count = len(report["human_review"])

    print("\n" + "=" * 60)
    print("🏁 AGENT COMPLETE")
    print(f"   Run ID          : {report['run_id']}")
    print(f"   Categories      : {report['categories_analyzed']} analyzed, {report['categories_flagged']} flagged")
    print(f"   Auto-applying   : {auto_count} HIGH confidence decisions")
    print(f"   Human review    : {review_count} decisions queued")
    print("=" * 60)

    return report


if __name__ == "__main__":
    report = run_agent()
    print("\n--- FINAL PRICING DECISION REPORT ---")
    print(json.dumps(report, indent=2))
