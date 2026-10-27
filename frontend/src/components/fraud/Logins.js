"use client";

import { useApi } from "../../lib/api";
import { ago } from "../../lib/format";
import { Async, Badge, Panel, Table } from "../ui";

export default function Logins() {
  const state = useApi("/fraud/logins", { refresh: 30_000 });
  const columns = [
    { key: "ip_address", label: "IP address" },
    { key: "failed_attempts", label: "Failed", align: "right" },
    { key: "successful_logins", label: "Succeeded", align: "right" },
    { key: "users_targeted", label: "Customers tried", align: "right" },
    { key: "last_attempt", label: "Last attempt", render: (r) => ago(r.last_attempt) },
    { key: "is_blocked", label: "Status", render: (r) => <Badge tone={r.is_blocked ? "bad" : ""}>{r.is_blocked ? "Blocked" : "Open"}</Badge> },
  ];
  return (
    <Panel
      title="Sign-in attempts, last 24 hours"
      note="Five failed attempts from one address within ten minutes block that address automatically for an hour."
      flush
    >
      <Async state={state} what="Loading sign-in attempts">
        {(rows) => <Table columns={columns} rows={rows} rowKey="ip_address" empty="No sign-in attempts recorded." />}
      </Async>
    </Panel>
  );
}
