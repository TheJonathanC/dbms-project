"use client";

import Link from "next/link";
import { useApi } from "../../lib/api";
import { ago, clock, moneyShort, money, plural } from "../../lib/format";
import { Async, Badge, Empty, Figures, PageHeader, Panel, Status } from "../../components/ui";
import VolumeChart, { ChartLegend } from "../../components/VolumeChart";

// The one sentence the page opens with: what needs a person right now.
function headline(k) {
  if (k.awaiting_review > 0) {
    return { text: `${plural(k.awaiting_review, "transfer is", "transfers are")} waiting for your decision.`, href: "/fraud", cta: "Review held transfers" };
  }
  if (k.open_alerts > 0) {
    return { text: `No transfers are on hold. ${plural(k.open_alerts, "alert is", "alerts are")} still open.`, href: "/fraud", cta: "Open the alerts" };
  }
  return { text: "Nothing needs your attention right now.", calm: true };
}

function riskTone(score) {
  return score >= 10 ? "bad" : score >= 4 ? "warn" : "";
}

export default function Overview() {
  const state = useApi("/overview", { refresh: 30_000 });

  return (
    <>
      <PageHeader title="Overview" />
      <Async state={state} what="Loading the overview">
        {({ kpis: k, health: h, hourly, alerts, risky }) => {
          const head = headline(k);
          return (
            <div className="stack">
              <section>
                <p className={head.calm ? "headline headline-calm" : "headline"}>{head.text}</p>
                <div className="status-line">
                  {head.href ? <Link className="btn primary" href={head.href}>{head.cta}</Link> : null}
                  <Status value={h.system_mode} />
                  <span>
                    {h.gateways_up} of {h.gateways_total} payment gateways up, {plural(h.dead_letters_pending, "failed job", "failed jobs")} waiting
                  </span>
                  <span className="faint">Checked {ago(h.checked_at)}</span>
                </div>
              </section>

              <Figures
                items={[
                  { label: "Accounts", value: k.total_accounts, sub: `${k.restricted_accounts} frozen or flagged` },
                  { label: "Money held", value: moneyShort(k.total_deposits) },
                  { label: "Moved in 24 hours", value: moneyShort(k.amount_moved_24h), sub: `${k.successful_24h} of ${k.transactions_24h} transfers completed` },
                  { label: "Fraud alerts in 24 hours", value: k.fraud_alerts_24h, sub: `${k.open_alerts} still open` },
                  { label: "Blocked IP addresses", value: k.blocked_ips },
                ]}
              />

              <Panel title="Transfers per hour" note="Last 24 hours, by outcome" action={<ChartLegend />}>
                <VolumeChart hourly={hourly} />
              </Panel>

              <div className="grid-2">
                <Panel title="Latest fraud alerts" action={<Link href="/fraud">All alerts</Link>} flush>
                  {alerts.length === 0 ? (
                    <Empty title="No alerts yet">Transfers that break a rule will show up here.</Empty>
                  ) : (
                    <ul className="list">
                      {alerts.map((a) => (
                        <li key={a.alert_id}>
                          <div className="list-main">
                            <div className="list-title">{a.rule_name}</div>
                            <div className="list-meta">
                              {a.sender_name} to {a.receiver_name}, {ago(a.raised_at)}
                            </div>
                          </div>
                          <div className="list-aside">
                            <div className="num">{money(a.amount)}</div>
                            <Status value={a.action_taken} />
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </Panel>

                <Panel title="Accounts to watch" note="Ranked by risk score" action={<Link href="/accounts">All accounts</Link>} flush>
                  {risky.length === 0 ? (
                    <Empty title="No risky accounts">Accounts with blocked transfers or failed logins appear here.</Empty>
                  ) : (
                    <ul className="list">
                      {risky.map((r) => (
                        <li key={r.account_id}>
                          <div className="list-main">
                            <div className="list-title">{r.full_name}</div>
                            <div className="list-meta">
                              {r.blocked_or_held} blocked or held, {r.failed_logins_24h} failed logins
                            </div>
                          </div>
                          <div className="list-aside">
                            <Badge tone={riskTone(r.risk_score)}>Risk {r.risk_score}</Badge>
                            <div className="small faint">{r.account_status === "ACTIVE" ? "" : <Status value={r.account_status} />}</div>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </Panel>
              </div>
              <p className="small faint">Last transfer activity: {hourly.length ? clock(hourly[hourly.length - 1].hour) : "none yet"}. This page refreshes every 30 seconds.</p>
            </div>
          );
        }}
      </Async>
    </>
  );
}
