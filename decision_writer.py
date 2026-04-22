from dotenv import load_dotenv
load_dotenv()

"""
decision_writer.py
------------------
The HANDS of the pricing-decision-agent.

Takes the structured pricing decision report produced by agent.py (the Brain)
and persists it in two places:
  1. Local JSON file       — immediate human review, debugging, audit trail
  2. Databricks Delta tables — decisions ARE data, store them like data engineers do

Two Delta tables are created:
  pricing_decisions      — one row per agent run (the executive summary)
  pricing_actions        — one row per category decision (the detail, queryable by category)

Why two tables?
  Think of it like a hospital:
  - pricing_decisions  = the admission record  (what happened in this run overall)
  - pricing_actions    = the treatment log      (what was decided for each patient/category)

  This separation lets you answer two different questions:
  "How many runs resulted in AUTO-APPLY this month?" → pricing_decisions
  "What has happened to Home & Kitchen pricing over time?" → pricing_actions
"""

import os
import json
import requests
import time
from datetime import datetime, timezone
from databricks.sdk import WorkspaceClient

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPORT_DIR         = "reports"
DECISIONS_TABLE    = "workspace.ecommerce.pricing_decisions"
ACTIONS_TABLE      = "workspace.ecommerce.pricing_actions"


# ---------------------------------------------------------------------------
# Databricks helpers
# ---------------------------------------------------------------------------

def _get_client():
    return WorkspaceClient()


def _get_warehouse_id(client) -> str:
    http_path = os.environ.get("DATABRICKS_HTTP_PATH", "")
    if http_path:
        return http_path.strip("/").split("/")[-1]
    warehouses = client.warehouses.list()
    for wh in warehouses:
        if wh.state.value in ("RUNNING", "IDLE"):
            return wh.id
    raise RuntimeError("No running SQL warehouse found.")


def _execute_sql(client, sql: str, warehouse_id: str):
    host = client.config.host
    token = client.config.token
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = requests.post(
        f"{host}/api/2.0/sql/statements",
        headers=headers,
        json={
            "statement": sql,
            "warehouse_id": warehouse_id,
            "wait_timeout": "30s",
            "on_wait_timeout": "CONTINUE",
        },
    )
    resp.raise_for_status()
    payload = resp.json()
    statement_id = payload["statement_id"]

    for _ in range(30):
        state = payload.get("status", {}).get("state", "")
        if state in ("SUCCEEDED", "FAILED", "CANCELED", "CLOSED"):
            break
        time.sleep(2)
        poll = requests.get(
            f"{host}/api/2.0/sql/statements/{statement_id}",
            headers=headers,
        )
        poll.raise_for_status()
        payload = poll.json()

    state = payload.get("status", {}).get("state")
    if state != "SUCCEEDED":
        error = payload.get("status", {}).get("error", {})
        raise RuntimeError(f"SQL failed [{state}]: {error.get('message', 'unknown')}")


# ---------------------------------------------------------------------------
# Table creation
# ---------------------------------------------------------------------------

def _ensure_tables_exist(client, warehouse_id: str):
    """Create both Delta tables if they don't already exist."""

    decisions_sql = f"""
    CREATE TABLE IF NOT EXISTS {DECISIONS_TABLE} (
        run_id                  STRING,
        generated_at            TIMESTAMP,
        categories_analyzed     INT,
        categories_flagged      INT,
        auto_apply_count        INT,
        human_review_count      INT,
        market_summary          STRING,
        action_summary          STRING,
        signals_collected_at    TIMESTAMP
    )
    USING DELTA
    COMMENT 'One row per pricing agent run. Executive summary of each decisioning cycle.'
    """

    actions_sql = f"""
    CREATE TABLE IF NOT EXISTS {ACTIONS_TABLE} (
        run_id                  STRING,
        generated_at            TIMESTAMP,
        category                STRING,
        opportunity_type        STRING,
        current_avg_price       DOUBLE,
        adjustment_pct          DOUBLE,
        new_price               DOUBLE,
        confidence              STRING,
        auto_applied            BOOLEAN,
        reasoning               STRING,
        risk_factors            STRING,
        expected_impact         STRING,
        reason_for_review       STRING
    )
    USING DELTA
    COMMENT 'One row per category decision per run. Query by category to see pricing history over time.'
    """

    _execute_sql(client, decisions_sql.strip(), warehouse_id)
    print(f"   ✅ Delta table ready: {DECISIONS_TABLE}")

    _execute_sql(client, actions_sql.strip(), warehouse_id)
    print(f"   ✅ Delta table ready: {ACTIONS_TABLE}")


# ---------------------------------------------------------------------------
# Writer 1 — Local JSON file
# ---------------------------------------------------------------------------

def write_to_file(report: dict) -> str:
    """Write the full pricing decision report to a timestamped JSON file."""
    os.makedirs(REPORT_DIR, exist_ok=True)
    run_id = report.get("run_id", f"pricing-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}")
    filename = f"{REPORT_DIR}/{run_id}.json"
    with open(filename, "w") as f:
        json.dump(report, f, indent=2)
    print(f"   📄 Report written to file: {filename}")
    return filename


# ---------------------------------------------------------------------------
# Writer 2 — Databricks Delta tables
# ---------------------------------------------------------------------------

def _esc(val) -> str:
    """Escape single quotes for SQL string literals."""
    return str(val).replace("'", "\\'") if val is not None else ""


def _fmt_ts(ts_str: str) -> str:
    """Format ISO timestamp for Delta TIMESTAMP insert."""
    return str(ts_str)[:19].replace("T", " ")


def write_to_delta(report: dict):
    """Insert pricing decisions into both Delta tables."""
    print(f"\n   🔌 Connecting to Databricks Delta...")
    client = _get_client()
    warehouse_id = _get_warehouse_id(client)

    _ensure_tables_exist(client, warehouse_id)

    run_id       = report.get("run_id")
    generated_at = _fmt_ts(report.get("generated_at", datetime.now(timezone.utc).isoformat()))
    signals_at   = _fmt_ts(report.get("signals_collected_at", datetime.now(timezone.utc).isoformat()))

    auto_apply   = report.get("auto_apply", [])
    human_review = report.get("human_review", [])
    all_recs     = report.get("all_recommendations", [])

    # ── Insert into pricing_decisions (one row per run) ────────────
    decisions_sql = f"""
    INSERT INTO {DECISIONS_TABLE} VALUES (
        '{run_id}',
        TIMESTAMP '{generated_at}',
        {report.get('categories_analyzed', 0)},
        {report.get('categories_flagged', 0)},
        {len(auto_apply)},
        {len(human_review)},
        '{_esc(report.get("market_summary", ""))}',
        '{_esc(report.get("action_summary", ""))}',
        TIMESTAMP '{signals_at}'
    )
    """
    _execute_sql(client, decisions_sql.strip(), warehouse_id)
    print(f"   ✅ Run summary inserted into: {DECISIONS_TABLE}")

    # ── Insert into pricing_actions (one row per category) ─────────
    # Build a unified view of all recommendations with auto_apply flag
    auto_cats = {a["category"]: a for a in auto_apply}
    review_cats = {r["category"]: r for r in human_review}

    for rec in all_recs:
        cat             = rec.get("category", "")
        auto_applied    = rec.get("confidence") == "HIGH"
        auto_detail     = auto_cats.get(cat, {})
        review_detail   = review_cats.get(cat, {})

        expected_impact   = _esc(auto_detail.get("expected_impact", ""))
        reason_for_review = _esc(review_detail.get("reason_for_review", ""))

        actions_sql = f"""
        INSERT INTO {ACTIONS_TABLE} VALUES (
            '{run_id}',
            TIMESTAMP '{generated_at}',
            '{_esc(cat)}',
            '{_esc(rec.get("opportunity_type", ""))}',
            {rec.get("current_avg_price") or 0.0},
            {rec.get("recommended_adjustment_pct") or 0.0},
            {rec.get("recommended_new_price") or 0.0},
            '{rec.get("confidence", "")}',
            {str(auto_applied).upper()},
            '{_esc(rec.get("reasoning", ""))}',
            '{_esc(rec.get("risk_factors", ""))}',
            '{expected_impact}',
            '{reason_for_review}'
        )
        """
        _execute_sql(client, actions_sql.strip(), warehouse_id)
        applied_label = "AUTO-APPLIED" if auto_applied else "HUMAN REVIEW"
        print(f"   ✅ [{applied_label}] {cat} → {ACTIONS_TABLE}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def write_decisions(report: dict):
    """
    Persist the pricing decision report to both destinations.
    Called by run_agent.py after the agentic loop completes.
    """
    print("\n✍️  Writing pricing decisions...")

    # 1. Local file — always written first
    file_path = write_to_file(report)

    # 2. Delta tables — decisions are data
    try:
        write_to_delta(report)
    except Exception as e:
        print(f"   ⚠️  Delta write failed (file report still saved): {e}")

    auto_count   = len(report.get("auto_apply", []))
    review_count = len(report.get("human_review", []))

    print(f"\n✅ Decisions persisted successfully.")
    print(f"   Auto-applied   : {auto_count} categories")
    print(f"   Human review   : {review_count} categories")
    print(f"   File           : {file_path}")
    print(f"   Delta tables   : {DECISIONS_TABLE}")
    print(f"                    {ACTIONS_TABLE}")


if __name__ == "__main__":
    # Quick test with a dummy report
    dummy = {
        "run_id": "pricing-test-001",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "signals_collected_at": datetime.now(timezone.utc).isoformat(),
        "categories_analyzed": 7,
        "categories_flagged": 2,
        "market_summary": "Test run — pricing looks mostly healthy.",
        "action_summary": "One auto-apply, one for review.",
        "auto_apply": [
            {
                "category": "Home & Kitchen",
                "adjustment_pct": -12,
                "new_price": 166.89,
                "expected_impact": "Reduce return rate from 16% to ~13%",
                "confidence": "HIGH"
            }
        ],
        "human_review": [
            {
                "category": "Beauty",
                "adjustment_pct": 20,
                "suggested_new_price": 47.84,
                "confidence": "MEDIUM",
                "reason_for_review": "Aggressive increase needs A/B testing first"
            }
        ],
        "all_recommendations": [
            {
                "category": "Home & Kitchen",
                "opportunity_type": "REDUCE_PRICE",
                "current_avg_price": 189.65,
                "recommended_adjustment_pct": -12,
                "recommended_new_price": 166.89,
                "confidence": "HIGH",
                "reasoning": "Highest return rate with high AOV — pricing resistance confirmed.",
                "risk_factors": "Could be quality issue",
                "auto_apply": True,
            },
            {
                "category": "Beauty",
                "opportunity_type": "INCREASE_PRICE",
                "current_avg_price": 39.87,
                "recommended_adjustment_pct": 20,
                "recommended_new_price": 47.84,
                "confidence": "MEDIUM",
                "reasoning": "Strong volume, low return rate — underpriced.",
                "risk_factors": "Price sensitivity in beauty category",
                "auto_apply": False,
            }
        ],
    }
    write_decisions(dummy)
