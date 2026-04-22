from dotenv import load_dotenv
load_dotenv()

"""
collect_signals.py
------------------
The EYES of the pricing-decision-agent.

Pulls three signal sources from the Databricks lakehouse:
  1. gold_revenue_by_category  — revenue, units, order count per category
  2. gold_return_analysis      — return rates and revenue lost per category
  3. bronze_products           — base prices per category (for context)

Joins and enriches these into a single signals dict ready for the Brain.

Why three sources?
  A single signal is misleading. High returns could mean bad quality OR wrong
  price point. Low revenue could mean low demand OR overpriced. The agent needs
  all three lenses simultaneously to reason correctly about what to do.
"""

import os
import time
import requests
from datetime import datetime, timezone
from databricks.sdk import WorkspaceClient


# ---------------------------------------------------------------------------
# Databricks connection helpers
# ---------------------------------------------------------------------------

def _get_client():
    """Auto-detect credentials via Databricks SDK."""
    return WorkspaceClient()


def _get_warehouse_id(client) -> str:
    """Return warehouse ID from HTTP path env var or auto-discovery."""
    http_path = os.environ.get("DATABRICKS_HTTP_PATH", "")
    if http_path:
        return http_path.strip("/").split("/")[-1]
    warehouses = client.warehouses.list()
    for wh in warehouses:
        if wh.state.value in ("RUNNING", "IDLE"):
            return wh.id
    raise RuntimeError("No running SQL warehouse found. Start one in your Databricks workspace.")


def _execute_sql(client, sql: str, warehouse_id: str) -> list:
    """Run a SQL statement and return rows as list of dicts."""
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
        raise RuntimeError(f"SQL failed [{state}]: {error.get('message', 'unknown error')}")

    schema = payload.get("manifest", {}).get("schema", {}).get("columns", [])
    col_names = [c["name"] for c in schema]
    rows = payload.get("result", {}).get("data_array", [])
    return [dict(zip(col_names, row)) for row in rows]


# ---------------------------------------------------------------------------
# Signal collectors
# ---------------------------------------------------------------------------

def get_revenue_signals(client, warehouse_id: str) -> list:
    """Pull revenue, units, and order count per category."""
    sql = """
        SELECT
            category,
            ROUND(total_revenue, 2)     AS total_revenue,
            total_units,
            ROUND(avg_order_value, 2)   AS avg_order_value,
            order_count
        FROM workspace.ecommerce.gold_revenue_by_category
        ORDER BY total_revenue DESC
    """
    rows = _execute_sql(client, sql.strip(), warehouse_id)
    print(f"   ✅ Revenue signals: {len(rows)} categories")
    return rows


def get_return_signals(client, warehouse_id: str) -> list:
    """Pull return rates and revenue lost per category."""
    sql = """
        SELECT
            category,
            total_orders,
            total_returns,
            ROUND(return_rate, 4)           AS return_rate,
            ROUND(total_revenue_lost, 2)    AS total_revenue_lost
        FROM workspace.ecommerce.gold_return_analysis
        ORDER BY return_rate DESC
    """
    rows = _execute_sql(client, sql.strip(), warehouse_id)
    print(f"   ✅ Return signals: {len(rows)} categories")
    return rows


def get_price_signals(client, warehouse_id: str) -> list:
    """Pull average base price per category from bronze_products."""
    sql = """
        SELECT
            category,
            COUNT(product_id)               AS product_count,
            ROUND(AVG(CAST(base_price AS DOUBLE)), 2) AS avg_base_price,
            ROUND(MIN(CAST(base_price AS DOUBLE)), 2) AS min_base_price,
            ROUND(MAX(CAST(base_price AS DOUBLE)), 2) AS max_base_price
        FROM workspace.ecommerce.bronze_products
        GROUP BY category
        ORDER BY avg_base_price DESC
    """
    rows = _execute_sql(client, sql.strip(), warehouse_id)
    print(f"   ✅ Price signals: {len(rows)} categories")
    return rows


# ---------------------------------------------------------------------------
# Signal enrichment — join all three sources by category
# ---------------------------------------------------------------------------

def enrich_signals(revenue: list, returns: list, prices: list) -> list:
    """
    Join revenue, return, and price signals by category into
    a single enriched record per category ready for the Brain.
    """
    # Build lookup dicts keyed by category
    returns_by_cat = {r["category"]: r for r in returns}
    prices_by_cat  = {p["category"]: p for p in prices}

    enriched = []
    for rev in revenue:
        cat = rev["category"]
        ret = returns_by_cat.get(cat, {})
        prc = prices_by_cat.get(cat, {})

        enriched.append({
            "category":             cat,
            # Revenue signals
            "total_revenue":        rev.get("total_revenue"),
            "total_units":          rev.get("total_units"),
            "avg_order_value":      rev.get("avg_order_value"),
            "order_count":          rev.get("order_count"),
            # Return signals
            "total_orders":         ret.get("total_orders"),
            "total_returns":        ret.get("total_returns"),
            "return_rate":          ret.get("return_rate"),
            "total_revenue_lost":   ret.get("total_revenue_lost"),
            # Price signals
            "product_count":        prc.get("product_count"),
            "avg_base_price":       prc.get("avg_base_price"),
            "min_base_price":       prc.get("min_base_price"),
            "max_base_price":       prc.get("max_base_price"),
        })

    return enriched


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def collect_all_signals() -> dict:
    """
    Collect and enrich all pricing signals across categories.
    Returns a structured dict ready to pass to the Brain (agent.py).
    """
    print("🔌 Connecting to Databricks...")
    client = _get_client()
    warehouse_id = _get_warehouse_id(client)
    print(f"✅ Connected. Using warehouse: {warehouse_id}\n")

    print("📊 Collecting pricing signals...")
    revenue = get_revenue_signals(client, warehouse_id)
    returns = get_return_signals(client, warehouse_id)
    prices  = get_price_signals(client, warehouse_id)

    enriched = enrich_signals(revenue, returns, prices)

    signals = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "category_count": len(enriched),
        "categories": enriched,
    }

    print(f"\n✅ Signals collected for {len(enriched)} categories.")
    return signals


if __name__ == "__main__":
    import json
    signals = collect_all_signals()
    print("\n--- RAW SIGNALS SNAPSHOT ---")
    print(json.dumps(signals, indent=2))
