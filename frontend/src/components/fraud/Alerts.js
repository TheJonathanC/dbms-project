"use client";

import { useApi } from "../../lib/api";
import { clock, money } from "../../lib/format";
import { Async, Panel, Status, Table } from "../ui";

export default function Alerts() {
  const state = useApi("/fraud/alerts?limit=200", { refresh: 30_000 });
  const columns = [
    { key: "raised_at", label: "When", nowrap: true, render: (a) => clock(a.raised_at) },
    { key: "rule_name", label: "Rule", render: (a) => <>{a.rule_name}<span className="sub">{a.evaluation_metric}</span></> },
    { key: "action_taken", label: "Action", render: (a) => <Status value={a.action_taken} /> },
    { key: "sender_name", label: "From" },
    { key: "receiver_name", label: "To" },
    { key: "amount", label: "Amount", align: "right", render: (a) => money(a.amount) },
    { key: "transaction_status", label: "Transfer", render: (a) => <Status value={a.transaction_status} /> },
    {
      key: "review_status",
      label: "Review",
      render: (a) => (
        <>
          <Status value={a.review_status} />
          {a.reviewed_by ? <span className="sub">by {a.reviewed_by}</span> : null}
        </>
      ),
    },
  ];
  return (
    <Panel flush>
      <Async state={state} what="Loading alerts">
        {(rows) => <Table columns={columns} rows={rows} rowKey="alert_id" empty="No alerts yet." />}
      </Async>
    </Panel>
  );
}
