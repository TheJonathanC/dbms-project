"use client";

import { useState } from "react";
import { api, errorText, request, useApi } from "../../../lib/api";
import { money, newKey, reason } from "../../../lib/format";
import { Async, Badge, Button, Field, PageHeader, Panel } from "../../../components/ui";

const OUTCOME = {
  SUCCESS: { text: "Money sent", tone: "good" },
  DUPLICATE: { text: "Already processed. Nothing was sent twice", tone: "good" },
  REJECTED: { text: "Rejected by the database", tone: "bad" },
  QUARANTINED: { text: "Held for review", tone: "warn" },
  DEFERRED: { text: "Waiting for a payment gateway", tone: "warn" },
  RETRYABLE: { text: "Busy. Try again in a moment", tone: "warn" },
  FAILED: { text: "Failed", tone: "bad" },
};

const BRUTE_IP = "203.0.113.99";

function findByName(accounts, name, type) {
  return accounts.find((a) => a.full_name.startsWith(name) && (!type || a.type_name === type)) || accounts.find((a) => a.full_name.startsWith(name));
}

function Form({ options }) {
  const { accounts, devices, rails } = options;
  const [form, setForm] = useState({ from: "", to: "", amount: "", rail: "UPI", pin: "4821", ip: "", device: "" });
  const [key, setKey] = useState(newKey());
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [brute, setBrute] = useState(null);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  const sender = accounts.find((a) => a.account_id === form.from);
  const senderDevices = devices.filter((d) => sender && d.user_id === sender.user_id);

  const scenarios = [
    { name: "Everyday payment", note: "Priya pays Aarav 1,500 by UPI.", pick: () => ({ from: findByName(accounts, "Priya")?.account_id, to: findByName(accounts, "Aarav")?.account_id, amount: "1500", rail: "UPI", pin: "4821", ip: "", device: "" }) },
    { name: "Over one lakh", note: "Sneha sends 1,50,000 to Karthik by RTGS. Large amounts are held.", pick: () => ({ from: findByName(accounts, "Sneha")?.account_id, to: findByName(accounts, "Karthik")?.account_id, amount: "150000", rail: "RTGS", pin: "4821", ip: "", device: "" }) },
    { name: "Pay a known mule account", note: "Priya pays Vikram, whose account is frozen and blocklisted.", pick: () => ({ from: findByName(accounts, "Priya")?.account_id, to: findByName(accounts, "Vikram")?.account_id, amount: "2000", rail: "UPI", pin: "4821", ip: "", device: "" }) },
    { name: "Blocklisted IP address", note: "A normal payment arriving from 185.220.101.45.", pick: () => ({ from: findByName(accounts, "Priya")?.account_id, to: findByName(accounts, "Aarav")?.account_id, amount: "500", rail: "UPI", pin: "4821", ip: "185.220.101.45", device: "" }) },
    { name: "Unknown device", note: "Aarav sends 30,000 from a laptop the bank has not seen.", pick: () => ({ from: findByName(accounts, "Aarav")?.account_id, to: findByName(accounts, "Priya")?.account_id, amount: "30000", rail: "UPI", pin: "4821", ip: "", device: "fp-aarav-new-laptop" }) },
    { name: "Wrong PIN", note: "The PIN is 0000. The failed attempt is recorded.", pick: () => ({ from: findByName(accounts, "Priya")?.account_id, to: findByName(accounts, "Aarav")?.account_id, amount: "500", rail: "UPI", pin: "0000", ip: "", device: "" }) },
  ];

  function load(s) {
    const p = s.pick();
    set({ ...p, from: p.from || "", to: p.to || "" });
    setKey(newKey());
    setResult(null);
  }

  async function send(e) {
    e?.preventDefault();
    setBusy(true);
    setResult(null);
    try {
      const res = await request("/transfers", {
        method: "POST",
        headers: { "idempotency-key": key },
        body: {
          sender_account_id: form.from,
          receiver_account_id: form.to,
          amount: Number(form.amount),
          rail: form.rail,
          pin: form.pin,
          source_ip: form.ip.trim() || null,
          device_hash: form.device.trim() || null,
        },
      });
      if (res.data?.outcome) setResult({ kind: "outcome", ...res.data, status: res.status });
      else setResult({ kind: "error", message: errorText(res), status: res.status });
    } catch {
      setResult({ kind: "error", message: "Could not reach the server. Check your connection and try again." });
    }
    setBusy(false);
  }

  async function bruteForce() {
    setBrute({ busy: true });
    try {
      const d = await api("/demo/failed-logins", { method: "POST", body: { ip: BRUTE_IP, count: 5 } });
      setBrute({ text: d.blocked ? `${d.ip} is now blocked. The database did it by itself after ${d.recorded} failed sign-ins.` : `${d.recorded} failed sign-ins recorded from ${d.ip}, but it is not blocked.`, tone: d.blocked ? "good" : "warn" });
    } catch (err) {
      setBrute({ text: err.message, tone: "bad" });
    }
  }

  const out = result?.kind === "outcome" ? OUTCOME[result.outcome] || { text: result.outcome, tone: "" } : null;
  const ready = form.from && form.to && form.from !== form.to && Number(form.amount) > 0 && form.pin.length >= 4;

  return (
    <div className="grid-2 uneven">
      <div className="stack">
        <Panel title="Transfer" note="This plays the part of a customer's app. The checks and the money movement run inside the database.">
          <form className="form-grid" onSubmit={send}>
            <Field label="From">
              <select className="input" value={form.from} onChange={(e) => set({ from: e.target.value, device: "" })} required>
                <option value="">Choose an account</option>
                {accounts.map((a) => (
                  <option key={a.account_id} value={a.account_id}>{a.full_name}, {a.type_name} ({money(a.balance)})</option>
                ))}
              </select>
            </Field>
            <Field label="To">
              <select className="input" value={form.to} onChange={(e) => set({ to: e.target.value })} required>
                <option value="">Choose an account</option>
                {accounts.filter((a) => a.account_id !== form.from).map((a) => (
                  <option key={a.account_id} value={a.account_id}>{a.full_name}, {a.type_name}{a.account_status !== "ACTIVE" ? ` (${a.account_status.toLowerCase()})` : ""}</option>
                ))}
              </select>
            </Field>
            <Field label="Amount (rupees)">
              <input className="input num" type="number" min="1" step="0.01" value={form.amount} onChange={(e) => set({ amount: e.target.value })} required />
            </Field>
            <Field label="Payment method">
              <select className="input" value={form.rail} onChange={(e) => set({ rail: e.target.value })}>
                {rails.map((r) => (
                  <option key={r.type_name} value={r.type_name}>{r.type_name}{r.requires_gateway ? " (needs a gateway)" : ""}</option>
                ))}
              </select>
            </Field>
            <Field label="PIN" hint="Every demo customer's PIN is 4821.">
              <input className="input" type="password" inputMode="numeric" autoComplete="off" value={form.pin} onChange={(e) => set({ pin: e.target.value })} required />
            </Field>
            <details className="wide">
              <summary>Pretend to be somewhere else</summary>
              <div className="form-grid" style={{ marginTop: 12 }}>
                <Field label="IP address" hint="Leave empty to use yours.">
                  <input className="input" value={form.ip} onChange={(e) => set({ ip: e.target.value })} placeholder="185.220.101.45" />
                </Field>
                <Field label="Device" hint="A device the customer has not used before triggers a rule.">
                  <input className="input" list="devices" value={form.device} onChange={(e) => set({ device: e.target.value })} placeholder="fp-aarav-new-laptop" />
                  <datalist id="devices">
                    {senderDevices.map((d) => (
                      <option key={d.fingerprint_hash} value={d.fingerprint_hash}>{d.device_label}{d.is_trusted ? "" : " (not trusted)"}</option>
                    ))}
                  </datalist>
                </Field>
                <Field label="Idempotency key" hint="Sending the same key again never moves the money twice." wide>
                  <span className="row">
                    <input className="input" style={{ flex: 1 }} value={key} onChange={(e) => setKey(e.target.value)} />
                    <Button small onClick={() => setKey(newKey())}>New key</Button>
                  </span>
                </Field>
              </div>
            </details>
            <div className="wide row">
              <Button variant="primary" type="submit" disabled={busy || !ready}>{busy ? "Sending..." : "Send money"}</Button>
              {result ? <Button onClick={() => send()} disabled={busy}>Send the same request again</Button> : null}
            </div>
          </form>
        </Panel>

        {result ? (
          <Panel title="Result">
            {out ? (
              <div className="stack">
                <div className="outcome">{out.text}</div>
                <dl className="kv">
                  {result.reason ? (<><dt>Why</dt><dd>{reason(result.reason)}</dd></>) : null}
                  {result.transaction_id ? (<><dt>Transaction</dt><dd><code>{result.transaction_id}</code></dd></>) : null}
                  <dt>Attempts</dt><dd>{result.attempts}</dd>
                  <dt>Status</dt><dd><Badge tone={out.tone}>{result.outcome}</Badge></dd>
                </dl>
              </div>
            ) : (
              <div className="notice bad" role="alert">{result.message}</div>
            )}
          </Panel>
        ) : null}
      </div>

      <div className="stack">
        <Panel title="Try a scenario" note="Fills in the form. Press Send money to run it." flush>
          {scenarios.map((s) => (
            <button key={s.name} type="button" className="scenario" onClick={() => load(s)}>
              <strong>{s.name}</strong>
              <span>{s.note}</span>
            </button>
          ))}
        </Panel>
        <Panel title="Brute-force a login" note={`Records 5 failed sign-ins from ${BRUTE_IP}. A database trigger blocklists the address on its own.`}>
          <div className="stack">
            <div><Button onClick={bruteForce} disabled={brute?.busy}>Simulate 5 failed logins</Button></div>
            {brute?.text ? <div className={`notice ${brute.tone}`} role="status">{brute.text}</div> : null}
          </div>
        </Panel>
      </div>
    </div>
  );
}

export default function Send() {
  const state = useApi("/transfers/options");
  return (
    <>
      <PageHeader title="Send money" lede="Create transfers and watch the database decide. Results show up on the other pages straight away." />
      <Async state={state} what="Loading accounts">{(options) => <Form options={options} />}</Async>
    </>
  );
}
