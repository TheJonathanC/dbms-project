"use client";

import { useState } from "react";
import { api, useApi } from "../../../lib/api";
import { money, plural } from "../../../lib/format";
import { Async, Badge, Button, Meter, PageHeader, Panel, Table } from "../../../components/ui";

const BAND_TONE = { HIGH: "bad", MEDIUM: "warn", LOW: "" };

export default function Analysis() {
  const [days, setDays] = useState(30);
  const state = useApi(`/analysis?days=${days}`);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  async function block(account) {
    if (!window.confirm(`Block ${account.name}? Their account will be frozen and no one can send to it.`)) return;
    setError("");
    setNotice("");
    try {
      await api("/blocklist/accounts", {
        method: "POST",
        body: { account_id: account.account_id, reason: `Analysis: ${account.reasons[0] || "high risk score"}`.slice(0, 255) },
      });
      setNotice(`${account.name} is blocked and frozen.`);
      state.reload();
    } catch (e) {
      setError(e.message);
    }
  }

  const accountColumns = [
    {
      key: "name",
      label: "Customer",
      render: (a) => (
        <>
          {a.name}
          <ul className="reasons">
            {a.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </>
      ),
    },
    {
      key: "score",
      label: "Risk",
      render: (a) => (
        <span className="row" style={{ gap: 8 }}>
          <Meter value={a.score} tone={BAND_TONE[a.band]} />
          <span className="num">{Math.round(a.score * 100)}</span>
          <Badge tone={BAND_TONE[a.band]}>{a.band === "HIGH" ? "High" : a.band === "MEDIUM" ? "Medium" : "Low"}</Badge>
        </span>
      ),
    },
    {
      key: "act",
      label: "",
      render: (a) =>
        a.blocked ? (
          <Badge tone="bad">Blocked</Badge>
        ) : a.band === "HIGH" ? (
          <Button small variant="danger" onClick={() => block(a)}>Block</Button>
        ) : null,
    },
  ];

  const flowColumns = [
    { key: "from", label: "From" },
    { key: "to", label: "To" },
    { key: "count", label: "Transfers", align: "right" },
    { key: "amount", label: "Total", align: "right", render: (f) => money(f.amount) },
    { key: "completed", label: "Money moved", render: (f) => (f.completed ? "Yes" : <Badge tone="warn">No</Badge>) },
  ];

  return (
    <>
      <PageHeader title="Analysis" lede="Follows the money between accounts and scores each customer. It reads completed and held transfers; nothing here changes data unless you press Block.">
        <select className="input compact" aria-label="Period" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
        </select>
        <Button onClick={state.reload}>Run again</Button>
      </PageHeader>

      <div className="stack">
        {notice ? <div className="notice good" role="status">{notice}</div> : null}
        {error ? <div className="notice bad" role="alert">{error}</div> : null}
        <Async state={state} what="Analysing transfers">
          {(d) => {
                        return (
              <>
                <div className="notice">
                  {d.summary.transactions === 0
                    ? `There are no transfers in the last ${d.window_days} days.`
                    : `${plural(d.summary.transactions, "transfer", "transfers")} in the last ${d.window_days} days. ${plural(d.summary.accounts_flagged, "account stands", "accounts stand")} out, ${d.summary.high_risk} of them high risk. Money loops: ${d.summary.cycles}. Accounts close to the blocklist: ${d.summary.near_blocklist}.`}
                </div>
                <div className="grid-2">
                  <Panel title="Customers by risk" note="Scores run from 0 to 100. The reasons say what raised each one." flush>
                    <Table columns={accountColumns} rows={d.accounts} rowKey="account_id" empty="No one stands out." />
                  </Panel>
                  <Panel title="Biggest money flows" note="Grouped by sender and receiver. Attempts that were blocked or held are marked." flush>
                    <Table columns={flowColumns} rows={d.flows.slice(0, 15)} empty="No transfers in this period." />
                  </Panel>
                </div>
              </>
            );
          }}
        </Async>
      </div>
    </>
  );
}
