"use client";

import { useState } from "react";
import { api, useApi } from "../../../lib/api";
import { ago, clock, money, sentence } from "../../../lib/format";
import { Async, Badge, Button, PageHeader, Panel, Status, Table } from "../../../components/ui";

export default function Operations() {
  const state = useApi("/ops", { refresh: 20_000 });
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  async function run(key, path, options, message) {
    setBusy(key);
    setNotice("");
    setError("");
    try {
      const res = await api(path, { method: "POST", ...options });
      setNotice(typeof message === "function" ? message(res) : message);
      state.reload();
    } catch (e) {
      setError(e.message);
    }
    setBusy("");
  }

  const gatewayColumns = [
    { key: "gateway_name", label: "Gateway", render: (g) => <>{g.gateway_name}<span className="sub">Priority {g.priority}</span></> },
    {
      key: "health_status",
      label: "Status",
      render: (g) => (
        <>
          <Status value={g.health_status} />
          {g.circuit_open ? <span className="sub">Paused until {clock(g.circuit_open_until)}</span> : null}
        </>
      ),
    },
    { key: "consecutive_failures", label: "Failures in a row", align: "right" },
    { key: "avg_latency_ms", label: "Speed", align: "right", render: (g) => (g.avg_latency_ms ? `${g.avg_latency_ms} ms` : "-") },
    {
      key: "act",
      label: "Simulate",
      render: (g) => (
        <span className="row" style={{ gap: 6 }}>
          <Button small disabled={!!busy} onClick={() => run(`f${g.gateway_id}`, `/ops/gateways/${g.gateway_id}/result`, { body: { success: false } }, `A failure was recorded for ${g.gateway_name}.`)}>Fail</Button>
          <Button small disabled={!!busy} onClick={() => run(`s${g.gateway_id}`, `/ops/gateways/${g.gateway_id}/result`, { body: { success: true } }, `${g.gateway_name} reported a success.`)}>Recover</Button>
        </span>
      ),
    },
  ];

  const queueColumns = [
    { key: "queue", label: "Queue", render: (r) => sentence(r.queue) },
    { key: "status", label: "Status", render: (r) => <Status value={r.status} /> },
    { key: "items", label: "Items", align: "right" },
    { key: "oldest", label: "Oldest", render: (r) => ago(r.oldest) },
  ];

  const deadColumns = [
    { key: "failed_at", label: "Failed", nowrap: true, render: (d) => clock(d.failed_at) },
    { key: "task_type", label: "Task", render: (d) => <>{sentence(d.task_type)}<span className="sub">{d.error_message}</span></> },
    { key: "retry_count", label: "Tries", align: "right", render: (d) => `${d.retry_count} of ${d.max_retries}` },
    { key: "status", label: "Status", render: (d) => <Status value={d.status} /> },
  ];

  const auditColumns = [
    { key: "recorded_at", label: "When", nowrap: true, render: (a) => clock(a.recorded_at) },
    { key: "full_name", label: "Customer" },
    { key: "source", label: "Source", render: (a) => (a.source === "DEPOSIT" ? "Deposit" : a.source) },
    { key: "change_amount", label: "Change", align: "right", render: (a) => (Number(a.change_amount) > 0 ? "+" : "") + money(a.change_amount) },
    { key: "new_balance", label: "Balance after", align: "right", render: (a) => money(a.new_balance) },
  ];

  return (
    <>
      <PageHeader title="Operations" lede="Health of the payment rails and background queues. Refreshes every 20 seconds.">
        <Button disabled={!!busy} onClick={() => run("house", "/ops/housekeeping", {}, "Housekeeping finished: expired holds and blocks were cleaned up.")}>Run housekeeping</Button>
        <Button
          disabled={!!busy}
          onClick={() => run("notify", "/ops/deliver-notifications", {}, (r) => `${r.sent} notification${r.sent === 1 ? "" : "s"} delivered, ${r.failed} failed.`)}
        >
          Deliver notifications
        </Button>
      </PageHeader>

      <div className="stack">
        {notice ? <div className="notice good" role="status">{notice}</div> : null}
        {error ? <div className="notice bad" role="alert">{error}</div> : null}
        <Async state={state} what="Loading operations">
          {(d) => (
            <>
              <div className="notice">
                System is <Status value={d.health?.system_mode} />. {d.health?.gateways_up} of {d.health?.gateways_total} gateways in service,{" "}
                {d.health?.stuck_pending_transactions} stuck transfers, {d.health?.dead_letters_pending} failed tasks waiting.
              </div>
              {d.ledger_mismatches.length > 0 ? (
                <div className="notice bad" role="alert">
                  Ledger check failed: {d.ledger_mismatches.length} account balance{d.ledger_mismatches.length === 1 ? " does" : "s do"} not match the audit history.
                </div>
              ) : (
                <div className="notice good">Ledger check passed: every balance matches its audit history.</div>
              )}
              <div className="grid-2">
                <Panel title="Payment gateways" note="Several failures in a row pause a gateway. Transfers then use the next one, or wait if none is left." flush>
                  <Table columns={gatewayColumns} rows={d.gateways} rowKey="gateway_id" />
                </Panel>
                <Panel title="Queues" flush>
                  <Table columns={queueColumns} rows={d.queues} empty="Both queues are empty." />
                </Panel>
              </div>
              <Panel title="Failed tasks" note="Work the system gave up on, kept for a person to look at." flush>
                <Table columns={deadColumns} rows={d.dead_letters} rowKey="dlq_id" empty="Nothing has failed." />
              </Panel>
              <Panel title="Balance history" note="Every change to a balance, written by the database itself." flush>
                <Table columns={auditColumns} rows={d.audit} rowKey="audit_seq" empty="No balance changes yet." />
              </Panel>
            </>
          )}
        </Async>
      </div>
    </>
  );
}
