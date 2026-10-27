"use client";

import { useState } from "react";
import { api, useApi } from "../../lib/api";
import { clock } from "../../lib/format";
import { Async, Badge, Button, Field, Panel, Table } from "../ui";

function expiry(row) {
  if (!row.in_force) return <Badge>Lifted or expired</Badge>;
  return row.expires_at ? <Badge tone="warn">Until {clock(row.expires_at)}</Badge> : <Badge tone="bad">Permanent</Badge>;
}

function IpForm({ onDone }) {
  const [ip, setIp] = useState("");
  const [why, setWhy] = useState("");
  const [hours, setHours] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/blocklist/ips", { method: "POST", body: { ip_address: ip.trim(), reason: why.trim(), ttl_hours: hours ? Number(hours) : null } });
      setIp("");
      setWhy("");
      setHours("");
      onDone();
    } catch (err) {
      setError(err.message);
    }
    setBusy(false);
  }

  return (
    <form className="form-grid" onSubmit={submit}>
      <Field label="IP address"><input className="input" value={ip} onChange={(e) => setIp(e.target.value)} placeholder="203.0.113.7" required /></Field>
      <Field label="Block for (hours)" hint="Leave empty for a permanent block."><input className="input" type="number" min="1" value={hours} onChange={(e) => setHours(e.target.value)} /></Field>
      <Field label="Reason" wide><input className="input" value={why} onChange={(e) => setWhy(e.target.value)} placeholder="Why is this address blocked?" minLength={3} required /></Field>
      {error ? <div className="notice bad wide" role="alert">{error}</div> : null}
      <div className="wide"><Button variant="primary" type="submit" disabled={busy || !ip || why.length < 3}>Block address</Button></div>
    </form>
  );
}

function AccountForm({ accounts, onDone }) {
  const [account, setAccount] = useState("");
  const [external, setExternal] = useState("");
  const [why, setWhy] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/blocklist/accounts", {
        method: "POST",
        body: { account_id: account || null, external_account_number: account ? null : external.trim() || null, reason: why.trim() },
      });
      setAccount("");
      setExternal("");
      setWhy("");
      onDone();
    } catch (err) {
      setError(err.message);
    }
    setBusy(false);
  }

  return (
    <form className="form-grid" onSubmit={submit}>
      <Field label="Account" hint={account ? "This account will be frozen automatically." : "Pick one of ours, or enter an outside account number."} wide>
        <select className="input" value={account} onChange={(e) => setAccount(e.target.value)}>
          <option value="">An account at another bank</option>
          {accounts.map((a) => (
            <option key={a.account_id} value={a.account_id}>{a.full_name}, {a.type_name}</option>
          ))}
        </select>
      </Field>
      {!account ? (
        <Field label="Outside account number" wide><input className="input" value={external} onChange={(e) => setExternal(e.target.value)} placeholder="EXT-1234567890" /></Field>
      ) : null}
      <Field label="Reason" wide><input className="input" value={why} onChange={(e) => setWhy(e.target.value)} placeholder="Why is this account blocked?" minLength={3} required /></Field>
      {error ? <div className="notice bad wide" role="alert">{error}</div> : null}
      <div className="wide"><Button variant="primary" type="submit" disabled={busy || why.length < 3 || (!account && !external)}>Block account</Button></div>
    </form>
  );
}

export default function Blocklist() {
  const state = useApi("/blocklist");
  const options = useApi("/transfers/options");
  const [error, setError] = useState("");

  async function lift(kind, id) {
    setError("");
    try {
      await api(`/blocklist/${kind}/${id}`, { method: "DELETE" });
      state.reload();
    } catch (e) {
      setError(e.message);
    }
  }

  const ipColumns = [
    { key: "ip_address", label: "Address", render: (r) => <>{r.ip_address}<span className="sub">{r.reason}</span></> },
    { key: "source", label: "Added by", render: (r) => ({ MANUAL: "An analyst", AUTO_BRUTE_FORCE: "The database (failed logins)", THREAT_FEED: "Threat feed" })[r.source] || r.source },
    { key: "expires_at", label: "Block", render: expiry },
    { key: "x", label: "", render: (r) => (r.in_force ? <Button small onClick={() => lift("ips", r.ip_block_id)}>Lift block</Button> : null) },
  ];
  const accountColumns = [
    { key: "who", label: "Account", render: (r) => <>{r.full_name || r.external_account_number}<span className="sub">{r.reason}</span></> },
    { key: "kind", label: "Where", render: (r) => (r.account_id ? "Our customer" : "Another bank") },
    { key: "expires_at", label: "Block", render: expiry },
    { key: "x", label: "", render: (r) => (r.in_force ? <Button small onClick={() => lift("accounts", r.block_id)}>Lift block</Button> : null) },
  ];

  return (
    <div className="stack">
      {error ? <div className="notice bad" role="alert">{error}</div> : null}
      <div className="grid-2">
        <Panel title="Blocked IP addresses" note="Transfers from these addresses are rejected." flush>
          <Async state={state} what="Loading the blocklist">{(d) => <Table columns={ipColumns} rows={d.ips} rowKey="ip_block_id" />}</Async>
        </Panel>
        <Panel title="Blocked accounts" note="Transfers to these accounts are rejected. Lifting a block does not unfreeze our own accounts." flush>
          <Async state={state} what="Loading the blocklist">{(d) => <Table columns={accountColumns} rows={d.accounts} rowKey="block_id" />}</Async>
        </Panel>
      </div>
      <div className="grid-2">
        <Panel title="Block an IP address"><IpForm onDone={state.reload} /></Panel>
        <Panel title="Block an account">
          <AccountForm accounts={options.data?.accounts || []} onDone={state.reload} />
        </Panel>
      </div>
    </div>
  );
}
