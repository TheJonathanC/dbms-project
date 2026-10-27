"""Offline fraud analytics.   Run:  python -m app.analytics.fraud_analysis --days 30 --top 15

The database already blocks the obvious patterns synchronously (fraud_rules). This
module looks for what single-transaction rules cannot see, using the whole history:

  1. Behavioural features per sender + Isolation Forest anomaly score
  2. Money-flow graph (networkx): pass-through "mule" nodes, fan-in / fan-out hubs,
     round-trip cycles, and guilt-by-association (<= 2 hops from a blocklisted account)
  3. Benford's-law test on transaction amounts (fabricated amounts deviate)
  4. Rule effectiveness from analyst reviews (precision per rule)
  5. A composite, explainable risk score per account

The analysis functions take plain DataFrames, so they are unit-testable without a
database; `load_frames` / `main` are the only parts that touch Neon (through the ORM).
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

FEATURE_COLS = ["txn_count", "total_amount", "mean_amount", "std_amount", "max_amount",
                "unique_receivers", "rejected_ratio", "night_ratio", "round_ratio",
                "near_ceiling_ratio", "max_txns_per_hour"]


# ------------------------------------------------------------------ 1. behavioural features
def sender_features(tx: pd.DataFrame, ceiling: float = 100_000.0) -> pd.DataFrame:
    """One row per sender. Expects columns: sender, receiver, amount, status, created_at."""
    if tx.empty:
        return pd.DataFrame(columns=FEATURE_COLS)
    df = tx.copy()
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
    hour_ist = df["created_at"].dt.tz_convert("Asia/Kolkata").dt.hour
    df["night"] = hour_ist.between(1, 4)
    df["round"] = (df["amount"] % 1000 == 0)
    df["near_ceiling"] = (df["amount"] >= 0.8 * ceiling) & (df["amount"] <= ceiling)
    df["held"] = df["status"].isin(["REJECTED", "QUARANTINED"])

    g = df.groupby("sender")
    feats = pd.DataFrame({
        "txn_count": g.size(),
        "total_amount": g["amount"].sum(),
        "mean_amount": g["amount"].mean(),
        "std_amount": g["amount"].std().fillna(0.0),
        "max_amount": g["amount"].max(),
        "unique_receivers": g["receiver"].nunique(),
        "rejected_ratio": g["held"].mean(),
        "night_ratio": g["night"].mean(),
        "round_ratio": g["round"].mean(),
        "near_ceiling_ratio": g["near_ceiling"].mean(),
    })
    burst = (df.sort_values("created_at").set_index("created_at")
               .groupby("sender")["amount"].rolling("1h").count()
               .groupby(level=0).max())
    feats["max_txns_per_hour"] = burst
    return feats.fillna(0.0)


def isolation_scores(feats: pd.DataFrame, min_rows: int = 8, seed: int = 42) -> pd.Series:
    """0..1 anomaly score per sender (1 = most unusual). Needs a minimum population to be meaningful."""
    if len(feats) < min_rows:
        return pd.Series(0.0, index=feats.index, name="iso_score")
    x = StandardScaler().fit_transform(feats[FEATURE_COLS].to_numpy(dtype=float))
    model = IsolationForest(n_estimators=200, contamination="auto", random_state=seed).fit(x)
    raw = -model.score_samples(x)                       # higher = more anomalous
    span = raw.max() - raw.min()
    scaled = (raw - raw.min()) / span if span > 0 else np.zeros_like(raw)
    return pd.Series(scaled, index=feats.index, name="iso_score")


# ------------------------------------------------------------------ 2. money-flow graph
def build_graph(tx: pd.DataFrame, statuses: tuple[str, ...] | None = ("SUCCESS",)) -> nx.DiGraph:
    df = tx if statuses is None else tx[tx["status"].isin(statuses)]
    G = nx.DiGraph()
    if df.empty:
        return G
    edges = df.groupby(["sender", "receiver"])["amount"].agg(["sum", "count"]).reset_index()
    for r in edges.itertuples(index=False):
        G.add_edge(r.sender, r.receiver, weight=float(r.sum), count=int(r.count))
    return G


@dataclass
class GraphFindings:
    pass_through: dict[str, dict] = field(default_factory=dict)
    fan_in: dict[str, dict] = field(default_factory=dict)
    fan_out: dict[str, dict] = field(default_factory=dict)
    cycles: list[list[str]] = field(default_factory=list)
    near_blocklist: dict[str, int] = field(default_factory=dict)      # account -> hop distance (1 or 2)


def graph_findings(tx: pd.DataFrame, blocklisted: set[str], min_flow: float = 50_000.0,
                   hub_degree: int = 4) -> GraphFindings:
    out = GraphFindings()
    G = build_graph(tx, ("SUCCESS",))

    for node in G.nodes:
        in_sum = sum(d["weight"] for _, _, d in G.in_edges(node, data=True))
        out_sum = sum(d["weight"] for _, _, d in G.out_edges(node, data=True))
        if in_sum >= min_flow and 0.85 * in_sum <= out_sum <= 1.10 * in_sum:
            out.pass_through[node] = dict(received=in_sum, forwarded=out_sum, ratio=round(out_sum / in_sum, 3))
        if G.in_degree(node) >= hub_degree and in_sum >= min_flow:
            out.fan_in[node] = dict(senders=G.in_degree(node), received=in_sum)
        if G.out_degree(node) >= hub_degree and out_sum >= min_flow:
            out.fan_out[node] = dict(receivers=G.out_degree(node), sent=out_sum)

    # round-tripping: a short cycle (2-4 hops) in which every leg is large and the legs are
    # nearly equal, i.e. the same money coming back to where it started. Requiring similar,
    # large legs keeps ordinary mutual payments between customers from being flagged.
    for cyc in itertools.islice(nx.simple_cycles(G, length_bound=4), 20_000):
        legs = [G[cyc[i]][cyc[(i + 1) % len(cyc)]]["weight"] for i in range(len(cyc))]
        if min(legs) >= min_flow and (max(legs) - min(legs)) / max(legs) <= 0.15:
            out.cycles.append(cyc)

    # guilt by association uses EVERY attempt (even rejected ones to a mule are a signal)
    G_any = build_graph(tx, None).to_undirected()
    for src in blocklisted:
        if src in G_any:
            for node, dist in nx.single_source_shortest_path_length(G_any, src, cutoff=2).items():
                if node not in blocklisted and dist > 0:
                    out.near_blocklist[node] = min(dist, out.near_blocklist.get(node, 9))
    return out


# ------------------------------------------------------------------ 3. Benford's law
def benford_test(amounts: pd.Series, min_n: int = 100) -> dict:
    a = amounts[amounts > 0]
    if len(a) < min_n:
        return dict(n=int(len(a)), tested=False, note=f"need at least {min_n} amounts")
    first = a.astype(float).map(lambda v: int(str(f"{v:.2f}").lstrip("0.")[0]))
    observed = first.value_counts().reindex(range(1, 10), fill_value=0).to_numpy(dtype=float)
    expected = np.array([math.log10(1 + 1 / d) for d in range(1, 10)]) * len(a)
    chi2 = float(((observed - expected) ** 2 / expected).sum())
    return dict(n=int(len(a)), tested=True, chi2=round(chi2, 2), critical_1pct=20.09,
                suspicious=bool(chi2 > 20.09))


# ------------------------------------------------------------------ 4. rule effectiveness
def rule_effectiveness(alerts: pd.DataFrame) -> pd.DataFrame:
    """Precision per rule from analyst reviews: confirmed / (confirmed + false positives)."""
    if alerts.empty:
        return pd.DataFrame(columns=["rule_name", "alerts", "confirmed", "false_positive", "open", "precision"])
    g = alerts.groupby("rule_name")
    res = pd.DataFrame({
        "alerts": g.size(),
        "confirmed": g["review_status"].apply(lambda s: int((s == "CONFIRMED").sum())),
        "false_positive": g["review_status"].apply(lambda s: int((s == "FALSE_POSITIVE").sum())),
        "open": g["review_status"].apply(lambda s: int((s == "OPEN").sum())),
    })
    reviewed = res["confirmed"] + res["false_positive"]
    res["precision"] = np.where(reviewed > 0, res["confirmed"] / reviewed.replace(0, np.nan), np.nan)
    return res.reset_index().sort_values("alerts", ascending=False)


# ------------------------------------------------------------------ 5. composite score
def score_accounts(feats: pd.DataFrame, iso: pd.Series, gf: GraphFindings,
                   alerts: pd.DataFrame, cycle_nodes: set[str] | None = None) -> pd.DataFrame:
    cycle_nodes = cycle_nodes if cycle_nodes is not None else {n for c in gf.cycles for n in c}
    held_by_sender = (alerts[alerts["action_taken"].isin(["BLOCK", "QUARANTINE"])]
                      .groupby("sender")["transaction_id"].nunique()) if not alerts.empty else pd.Series(dtype=int)

    accounts = set(feats.index) | set(gf.pass_through) | set(gf.fan_in) | set(gf.fan_out) \
        | cycle_nodes | set(gf.near_blocklist)
    rows = []
    for acc in accounts:
        pts, why = 0.0, []
        if acc in gf.pass_through:
            pts += 0.35; why.append(f"pass-through {gf.pass_through[acc]['ratio']:.0%} of inflow forwarded")
        if acc in gf.fan_in:
            pts += 0.20; why.append(f"fan-in from {gf.fan_in[acc]['senders']} senders")
        if acc in gf.fan_out:
            pts += 0.15; why.append(f"fan-out to {gf.fan_out[acc]['receivers']} receivers")
        if acc in cycle_nodes:
            pts += 0.30; why.append("part of a money round-trip cycle")
        if acc in gf.near_blocklist:
            d = gf.near_blocklist[acc]
            pts += 0.25 if d == 1 else 0.10
            why.append(f"{d} hop(s) from a blocklisted account")
        held = int(held_by_sender.get(acc, 0))
        if held:
            pts += min(0.30, 0.10 * held); why.append(f"{held} transfers blocked/held by the engine")
        if acc in feats.index:
            f = feats.loc[acc]
            if f["txn_count"] >= 3 and f["near_ceiling_ratio"] >= 0.5:
                pts += 0.30; why.append("repeated transfers just under the ceiling (structuring)")
            if f["txn_count"] >= 3 and f["night_ratio"] >= 0.5:
                pts += 0.10; why.append("mostly night-time activity")
        iso_s = float(iso.get(acc, 0.0))
        score = round(min(1.0, pts) * 0.7 + iso_s * 0.3, 3)
        if iso_s >= 0.8:
            why.append(f"statistical outlier (isolation score {iso_s:.2f})")
        rows.append(dict(account=acc, score=score, iso_score=round(iso_s, 3), rule_points=round(min(1.0, pts), 2),
                         risk_band="HIGH" if score >= 0.6 else "MEDIUM" if score >= 0.35 else "LOW",
                         reasons="; ".join(why)))
    out = pd.DataFrame(rows)
    return out.sort_values("score", ascending=False).reset_index(drop=True) if not out.empty else out


@dataclass
class AnalysisReport:
    scores: pd.DataFrame
    graph: GraphFindings
    benford: dict
    rules: pd.DataFrame
    generated_at: str

    def to_json(self) -> dict:
        return dict(
            generated_at=self.generated_at,
            benford=self.benford,
            pass_through=self.graph.pass_through, fan_in=self.graph.fan_in, fan_out=self.graph.fan_out,
            cycles=self.graph.cycles, near_blocklist=self.graph.near_blocklist,
            rule_effectiveness=self.rules.replace({np.nan: None}).to_dict(orient="records"),
            top_accounts=self.scores.head(25).to_dict(orient="records"),
        )


def analyse(tx: pd.DataFrame, alerts: pd.DataFrame, blocklisted: set[str],
            ceiling: float = 100_000.0) -> AnalysisReport:
    feats = sender_features(tx, ceiling)
    iso = isolation_scores(feats)
    gf = graph_findings(tx, blocklisted)
    scores = score_accounts(feats, iso, gf, alerts)
    success_amounts = tx.loc[tx["status"] == "SUCCESS", "amount"] if not tx.empty else pd.Series(dtype=float)
    return AnalysisReport(scores, gf, benford_test(success_amounts), rule_effectiveness(alerts),
                          datetime.now(timezone.utc).isoformat(timespec="seconds"))


# ------------------------------------------------------------------ database access (ORM) + CLI
def load_frames(days: int):
    from sqlalchemy import select

    from app.db import engine
    from app.models import BlocklistedAccount, FraudAlert, FraudRule, Transaction

    since = datetime.now(timezone.utc) - pd.Timedelta(days=days)
    tx_stmt = (select(Transaction.transaction_id, Transaction.sender_account_id.label("sender"),
                      Transaction.receiver_account_id.label("receiver"), Transaction.amount,
                      Transaction.status, Transaction.status_reason, Transaction.created_at, Transaction.type_id)
               .where(Transaction.created_at >= since))
    alert_stmt = (select(FraudAlert.transaction_id, FraudRule.rule_name, FraudAlert.action_taken,
                         FraudAlert.review_status, Transaction.sender_account_id.label("sender"))
                  .join(FraudRule, FraudRule.rule_id == FraudAlert.rule_id)
                  .join(Transaction, Transaction.transaction_id == FraudAlert.transaction_id)
                  .where(FraudAlert.raised_at >= since))
    block_stmt = select(BlocklistedAccount.account_id).where(BlocklistedAccount.is_active,
                                                             BlocklistedAccount.account_id.is_not(None))
    ceiling_stmt = select(FraudRule.threshold_value).where(FraudRule.evaluation_metric == "MAX_SINGLE_AMOUNT",
                                                           FraudRule.is_active)
    with engine.connect() as conn:
        tx = pd.read_sql(tx_stmt, conn)
        alerts = pd.read_sql(alert_stmt, conn)
        blocked = {str(r[0]) for r in conn.execute(block_stmt)}
        ceilings = [float(r[0]) for r in conn.execute(ceiling_stmt)]
    for col in ("transaction_id", "sender", "receiver"):
        if col in tx:
            tx[col] = tx[col].astype(str)
    for col in ("transaction_id", "sender"):
        if col in alerts:
            alerts[col] = alerts[col].astype(str)
    tx["amount"] = tx["amount"].astype(float)
    return tx, alerts, blocked, (min(ceilings) if ceilings else 100_000.0)


def apply_blocklist(scores: pd.DataFrame, blocked: set[str], min_score: float) -> list[str]:
    """Optional action: blocklist HIGH-risk accounts. The ORM insert fires the database trigger
    that freezes the account, and the config-change trigger that invalidates every cache."""
    import uuid as _uuid

    from app.db import session_scope
    from app.models import Account, BlocklistedAccount

    applied = []
    with session_scope() as s:
        for r in scores[scores["score"] >= min_score].itertuples():
            if r.account in blocked:
                continue
            acc = s.get(Account, _uuid.UUID(r.account))
            if acc is None or acc.account_status == "FROZEN":
                continue
            s.add(BlocklistedAccount(account_id=acc.account_id,
                                     reason=f"Python analytics (score {r.score}): {r.reasons}"[:255]))
            applied.append(r.account)
    return applied


def main() -> None:
    ap = argparse.ArgumentParser(description="Fraud analytics over the transaction history")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", default="reports")
    ap.add_argument("--apply", action="store_true", help="blocklist accounts at or above --min-score")
    ap.add_argument("--min-score", type=float, default=0.6)
    args = ap.parse_args()

    tx, alerts, blocked, ceiling = load_frames(args.days)
    print(f"Loaded {len(tx)} transactions, {len(alerts)} alerts, {len(blocked)} blocklisted accounts "
          f"(ceiling {ceiling:,.0f})")
    report = analyse(tx, alerts, blocked, ceiling)

    pd.set_option("display.width", 200, "display.max_colwidth", 90)
    print("\n== Top suspicious accounts ==")
    print(report.scores.head(args.top).to_string(index=False) if not report.scores.empty else "none")
    print("\n== Rule effectiveness (analyst-reviewed precision) ==")
    print(report.rules.to_string(index=False) if not report.rules.empty else "no alerts yet")
    print("\n== Benford's law on successful amounts ==")
    print(report.benford)
    print(f"\nGraph: {len(report.graph.pass_through)} pass-through, {len(report.graph.fan_in)} fan-in hubs, "
          f"{len(report.graph.fan_out)} fan-out hubs, {len(report.graph.cycles)} cycles, "
          f"{len(report.graph.near_blocklist)} accounts near the blocklist")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report.scores.to_csv(out / f"suspicious_accounts_{stamp}.csv", index=False)
    (out / f"fraud_report_{stamp}.json").write_text(json.dumps(report.to_json(), indent=2, default=str))
    print(f"\nSaved report to {out}/")

    if args.apply:
        done = apply_blocklist(report.scores, blocked, args.min_score)
        print(f"Blocklisted and froze {len(done)} account(s): {done}")


if __name__ == "__main__":
    main()
