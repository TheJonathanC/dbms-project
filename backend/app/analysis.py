"""Fraud analytics in Python.

The database already blocks the obvious patterns, one transaction at a time (fraud_rules). This module
looks at the whole history for what a single-transaction rule cannot see, by building a money-flow
graph with networkx:

  pass-through   an account that forwards almost everything it receives (a mule)
  fan-in         many different senders paying one account
  fan-out        one account paying many different receivers
  round trips    money that leaves an account and comes back through 2 to 4 hops
  near blocklist an account one or two hops away from a blocklisted account
  structuring    repeated transfers just under the single-transfer ceiling

Each finding adds points to an account's risk score (capped at 1.0) and a plain-language reason.
The functions take plain lists of dicts, so they can be tested without a database.
"""
from collections import defaultdict
from datetime import datetime, timezone

import networkx as nx


def _graph(tx: list[dict], only_status: str | None) -> nx.DiGraph:
    totals: dict[tuple[str, str], list] = defaultdict(lambda: [0.0, 0])
    for t in tx:
        if only_status and t["status"] != only_status:
            continue
        edge = totals[(t["sender"], t["receiver"])]
        edge[0] += t["amount"]
        edge[1] += 1
    g = nx.DiGraph()
    for (a, b), (amount, count) in totals.items():
        g.add_edge(a, b, weight=amount, count=count)
    return g


def analyse(tx: list[dict], alerts: list[dict], blocked: set[str], names: dict[str, str],
            ceiling: float = 100_000.0, min_flow: float = 50_000.0, hub_degree: int = 3) -> dict:
    """tx rows: sender, receiver, amount, status.  alerts rows: sender, transaction_id, action_taken."""
    paid = _graph(tx, "SUCCESS")      # money that really moved
    tried = _graph(tx, None)          # every attempt, including blocked ones

    points: dict[str, float] = defaultdict(float)
    why: dict[str, list[str]] = defaultdict(list)

    def flag(account: str, pts: float, reason: str) -> None:
        points[account] += pts
        why[account].append(reason)

    summary = dict(pass_through=0, fan_in=0, fan_out=0, cycles=0, near_blocklist=0, structuring=0)

    # --- graph patterns on money that moved
    for node in paid.nodes:
        received = sum(d["weight"] for _, _, d in paid.in_edges(node, data=True))
        sent = sum(d["weight"] for _, _, d in paid.out_edges(node, data=True))
        if received >= min_flow and 0.85 * received <= sent <= 1.10 * received:
            flag(node, 0.35, f"forwarded {sent / received:.0%} of the money it received")
            summary["pass_through"] += 1
        if paid.in_degree(node) >= hub_degree and received >= min_flow:
            flag(node, 0.20, f"received money from {paid.in_degree(node)} different accounts")
            summary["fan_in"] += 1
        if paid.out_degree(node) >= hub_degree and sent >= min_flow:
            flag(node, 0.15, f"sent money to {paid.out_degree(node)} different accounts")
            summary["fan_out"] += 1

    # --- round trips: a short cycle whose legs are large and nearly equal
    in_cycle: set[str] = set()
    for cycle in nx.simple_cycles(paid, length_bound=4):
        legs = [paid[cycle[i]][cycle[(i + 1) % len(cycle)]]["weight"] for i in range(len(cycle))]
        if min(legs) >= min_flow and (max(legs) - min(legs)) / max(legs) <= 0.15:
            in_cycle.update(cycle)
            summary["cycles"] += 1
    for node in in_cycle:
        flag(node, 0.30, "part of a loop where the same money returns to where it started")

    # --- closeness to the blocklist, counting every attempt (even a blocked payment to a mule is a signal)
    undirected = tried.to_undirected()
    near: dict[str, int] = {}
    for bad in blocked:
        if bad in undirected:
            for node, hops in nx.single_source_shortest_path_length(undirected, bad, cutoff=2).items():
                if hops > 0 and node not in blocked:
                    near[node] = min(hops, near.get(node, 9))
    for node, hops in near.items():
        flag(node, 0.25 if hops == 1 else 0.10, f"{hops} step(s) away from a blocklisted account")
        summary["near_blocklist"] += 1

    # --- blocked or held by the database rules
    held: dict[str, set[str]] = defaultdict(set)
    for a in alerts:
        if a["action_taken"] in ("BLOCK", "QUARANTINE"):
            held[a["sender"]].add(a["transaction_id"])
    for account, txs in held.items():
        flag(account, min(0.30, 0.10 * len(txs)), f"{len(txs)} transfer(s) blocked or held by the database rules")

    # --- structuring: repeated amounts just under the ceiling
    near_ceiling: dict[str, int] = defaultdict(int)
    for t in tx:
        if 0.8 * ceiling <= t["amount"] <= ceiling:
            near_ceiling[t["sender"]] += 1
    for account, n in near_ceiling.items():
        if n >= 3:
            flag(account, 0.30, f"{n} transfers just under the {ceiling:,.0f} ceiling")
            summary["structuring"] += 1

    accounts = []
    for account, pts in points.items():
        score = round(min(1.0, pts), 2)
        accounts.append({
            "account_id": account,
            "name": names.get(account, account[:8]),
            "score": score,
            "band": "HIGH" if score >= 0.6 else "MEDIUM" if score >= 0.35 else "LOW",
            "blocked": account in blocked,
            "reasons": why[account],
        })
    accounts.sort(key=lambda a: a["score"], reverse=True)

    flows = sorted(
        ({"from": names.get(a, a[:8]), "to": names.get(b, b[:8]),
          "amount": round(d["weight"], 2), "count": d["count"],
          "completed": paid.has_edge(a, b)}
         for a, b, d in tried.edges(data=True)),
        key=lambda f: f["amount"], reverse=True)[:12]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {"transactions": len(tx), "accounts_flagged": len(accounts),
                    "high_risk": sum(1 for a in accounts if a["band"] == "HIGH"), **summary},
        "accounts": accounts,
        "flows": flows,
    }


def run(days: int = 30) -> dict:
    """Load the history from the database and analyse it."""
    from sqlalchemy import text

    from . import queries as q
    from .db import rows, session_scope

    with session_scope() as s:
        tx = rows(s, q.ANALYSIS_TRANSACTIONS, days=days)
        alerts = rows(s, q.ANALYSIS_ALERTS, days=days)
        blocked = {r["account_id"] for r in rows(s, q.ANALYSIS_BLOCKED)}
        names = {r["account_id"]: r["name"] for r in rows(s, q.ANALYSIS_NAMES)}
        ceiling = s.execute(text(q.ANALYSIS_CEILING)).scalar_one_or_none() or 100_000.0
    result = analyse(tx, alerts, blocked, names, ceiling=float(ceiling))
    result["window_days"] = days
    return result
