from dotenv import load_dotenv
load_dotenv()

"""
run_agent.py
------------
The single entrypoint for the pricing-decision-agent.

Orchestrates the full pipeline:
  Eyes   → collect_signals.py    (what do the pricing signals look like?)
  Brain  → agent.py              (what should we do about it?)
  Hands  → decision_writer.py    (persist decisions, auto-apply HIGH confidence)

Usage:
  python run_agent.py              # full run
  python run_agent.py --dry-run    # Eyes + Brain only, skip writing decisions
  python run_agent.py --json       # output raw JSON report
"""

import argparse
import json
import sys
from datetime import datetime, timezone

from agent import run_agent
from decision_writer import write_decisions


def print_banner():
    print()
    print("=" * 60)
    print("  💰 PRICING DECISION AGENT")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 60)
    print()


def print_summary(report: dict):
    """Print a human-readable summary after the full run."""

    status = report.get("status", "")
    if status == "NO_ACTION":
        print("\n✅ All categories well-priced. No adjustments needed.")
        return

    auto_apply   = report.get("auto_apply", [])
    human_review = report.get("human_review", [])

    print()
    print("=" * 60)
    print(f"  📊 Categories analyzed : {report.get('categories_analyzed', 0)}")
    print(f"  🚩 Categories flagged  : {report.get('categories_flagged', 0)}")
    print(f"  🤖 Auto-applied        : {len(auto_apply)} HIGH confidence decisions")
    print(f"  👤 Human review queue  : {len(human_review)} decisions")
    print(f"  🕐 Run ID              : {report.get('run_id')}")
    print("=" * 60)

    summary = report.get("market_summary", "")
    if summary:
        print(f"\n  MARKET SUMMARY:\n  {summary}")

    if auto_apply:
        print(f"\n  🤖 AUTO-APPLIED (HIGH confidence):")
        for item in auto_apply:
            direction = "▼" if item.get("adjustment_pct", 0) < 0 else "▲"
            print(f"\n  {direction} {item.get('category')}")
            print(f"    Adjustment     : {item.get('adjustment_pct')}% → new price ${item.get('new_price')}")
            print(f"    Expected impact: {item.get('expected_impact')}")

    if human_review:
        print(f"\n  👤 HUMAN REVIEW QUEUE:")
        for item in human_review:
            confidence = item.get("confidence", "")
            emoji = "🟡" if confidence == "MEDIUM" else "🔴"
            direction = "▼" if item.get("adjustment_pct", 0) < 0 else "▲"
            print(f"\n  {emoji} {item.get('category')} [{confidence}]")
            print(f"    Suggestion     : {item.get('adjustment_pct')}% → ${item.get('suggested_new_price')}")
            print(f"    Reason         : {item.get('reason_for_review')}")

    action_summary = report.get("action_summary", "")
    if action_summary:
        print(f"\n  ACTION SUMMARY:\n  {action_summary}")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Pricing Decision Agent — autonomous pricing optimization powered by Claude API"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run Eyes + Brain only, skip writing decisions (useful for testing)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output the final report as raw JSON"
    )
    args = parser.parse_args()

    print_banner()

    # ── Eyes + Brain: run the full agentic loop ────────────────────
    try:
        report = run_agent()
    except Exception as e:
        print(f"\n❌ Agent failed: {e}")
        sys.exit(1)

    # ── Hands: persist decisions ───────────────────────────────────
    if args.dry_run:
        print("\n⚠️  Dry run mode — skipping decision write.")
    else:
        try:
            write_decisions(report)
        except Exception as e:
            print(f"\n⚠️  Decision write failed: {e}")

    # ── Output ─────────────────────────────────────────────────────
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_summary(report)

    # Exit with code 2 if any decisions were auto-applied
    # Useful for triggering downstream notifications in Databricks Jobs
    if report.get("auto_apply"):
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
