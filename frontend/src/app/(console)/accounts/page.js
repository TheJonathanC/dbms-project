"use client";

import { useState } from "react";
import { api, useApi } from "../../../lib/api";
import { money } from "../../../lib/format";
import { Async, Badge, Button, PageHeader, Panel, Status, Table } from "../../../components/ui";

function Risk({ score }) {
  if (!score) return <span className="faint">None</span>;
  const [name, tone] = score >= 10 ? ["High", "bad"] : score >= 4 ? ["Medium", "warn"] : ["Low", ""];
  return <Badge tone={tone}>{name} ({score})</Badge>;
}

export default function Accounts() {
  const state = useApi("/accounts", { refresh: 30_000 });
  const [sort, setSort] = useState("risk");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState(null);

  async function setStatus(account, status) {
    const verb = status === "FROZEN" ? "Freeze" : "Unfreeze";
    if (!window.confirm(`${verb} ${account.full_name}'s ${account.type_name} account?`)) return;
    setBusy(account.account_id);
    setMessage(null);
    try {
      await api(`/accounts/${account.account_id}/status`, { method: "POST", body: { status } });
      setMessage({ tone: "good", text: `${account.full_name}'s account is now ${status === "FROZEN" ? "frozen" : "active"}. The owner has been notified.` });
      state.reload();
    } catch (e) {
      setMessage({ tone: "bad", text: e.message });
    }
    setBusy("");
  }

  const columns = [
    {
      key: "full_name",
      label: "Account holder",
      render: (a) => (
        <>
          {a.full_name}
          <span className="sub">{a.type_name}, {a.branch_name}</span>
        </>
      ),
    },
    { key: "balance", label: "Balance", align: "right", render: (a) => money(a.balance) },
    {
      key: "sent_last_24h",
      label: "Sent in 24 h",
      align: "right",
      render: (a) => (
        <>
          {money(a.sent_last_24h)}
          <span className="sub">of {money(a.daily_limit)} daily limit</span>
        </>
      ),
    },
    { key: "kyc_verified", label: "KYC", render: (a) => <Badge tone={a.kyc_verified ? "good" : "warn"}>{a.kyc_verified ? "Verified" : "Pending"}</Badge> },
    { key: "account_status", label: "Status", render: (a) => <Status value={a.account_status} /> },
    { key: "risk_score", label: "Risk", render: (a) => <Risk score={a.risk_score} /> },
    {
      key: "action",
      label: "",
      render: (a) =>
        a.account_status === "ACTIVE" ? (
          <Button small variant="danger" disabled={busy === a.account_id} onClick={() => setStatus(a, "FROZEN")}>Freeze</Button>
        ) : (
          <Button small disabled={busy === a.account_id} onClick={() => setStatus(a, "ACTIVE")}>Unfreeze</Button>
        ),
    },
  ];

  return (
    <>
      <PageHeader title="Accounts" lede="Balances, limits and risk for every customer account.">
        <label className="row small muted" htmlFor="sort">
          Sort by
          <select id="sort" className="input compact" style={{ width: "auto" }} value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="risk">Highest risk</option>
            <option value="name">Name</option>
            <option value="balance">Balance</option>
          </select>
        </label>
      </PageHeader>
      <div className="stack">
        {message ? <div className={`notice ${message.tone}`} role="status">{message.text}</div> : null}
        <Panel flush>
          <Async state={state} what="Loading accounts">
            {(rows) => {
              const sorted = [...rows].sort((a, b) =>
                sort === "risk" ? b.risk_score - a.risk_score || a.full_name.localeCompare(b.full_name)
                : sort === "balance" ? b.balance - a.balance
                : a.full_name.localeCompare(b.full_name),
              );
              return <Table columns={columns} rows={sorted} rowKey="account_id" />;
            }}
          </Async>
        </Panel>
      </div>
    </>
  );
}
