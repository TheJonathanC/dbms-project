"use client";

import { useState } from "react";
import { useApi } from "../../../lib/api";
import { clock, money, reason } from "../../../lib/format";
import { Async, PageHeader, Panel, Status, Table } from "../../../components/ui";

const FILTERS = [
  { id: "", name: "All" },
  { id: "SUCCESS", name: "Completed" },
  { id: "QUARANTINED", name: "On hold" },
  { id: "REJECTED", name: "Rejected" },
  { id: "FAILED", name: "Failed" },
];

export default function Transactions() {
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const state = useApi(`/transactions?limit=300${status ? `&status=${status}` : ""}`, { refresh: 20_000 });

  const columns = [
    { key: "created_at", label: "When", nowrap: true, render: (t) => clock(t.created_at) },
    { key: "sender_name", label: "From" },
    { key: "receiver_name", label: "To" },
    { key: "type_name", label: "Rail" },
    { key: "amount", label: "Amount", align: "right", render: (t) => money(t.amount) },
    {
      key: "status",
      label: "Outcome",
      render: (t) => (
        <>
          <Status value={t.status} />
          {t.status_reason ? <span className="sub">{reason(t.status_reason)}</span> : null}
        </>
      ),
    },
  ];

  return (
    <>
      <PageHeader title="Transactions" lede="Every transfer attempt, including the ones the database blocked or held." />
      <div className="stack">
        <div className="row">
          <div className="chips" role="group" aria-label="Filter by outcome">
            {FILTERS.map((f) => (
              <button key={f.id} type="button" className="chip" aria-pressed={status === f.id} onClick={() => setStatus(f.id)}>
                {f.name}
              </button>
            ))}
          </div>
          <input
            className="input compact"
            style={{ maxWidth: 260 }}
            placeholder="Search by name"
            aria-label="Search by name"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <Panel flush>
          <Async state={state} what="Loading transactions">
            {(rows) => {
              const q = query.trim().toLowerCase();
              const shown = q ? rows.filter((t) => `${t.sender_name} ${t.receiver_name}`.toLowerCase().includes(q)) : rows;
              return <Table columns={columns} rows={shown} rowKey="transaction_id" empty="No transfers match." />;
            }}
          </Async>
        </Panel>
      </div>
    </>
  );
}
