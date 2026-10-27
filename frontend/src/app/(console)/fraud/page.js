"use client";

import { useState } from "react";
import { useApi } from "../../../lib/api";
import { PageHeader, Tabs } from "../../../components/ui";
import ReviewQueue from "../../../components/fraud/ReviewQueue";
import Alerts from "../../../components/fraud/Alerts";
import Rules from "../../../components/fraud/Rules";
import Blocklist from "../../../components/fraud/Blocklist";
import Logins from "../../../components/fraud/Logins";

export default function Fraud() {
  const [tab, setTab] = useState("queue");
  const queue = useApi("/fraud/queue", { refresh: 20_000 });
  const held = Array.isArray(queue.data) ? queue.data.length : 0;

  return (
    <>
      <PageHeader title="Fraud review" lede="Decide on held transfers, tune the rules, and manage what is blocked." />
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "queue", label: "Held transfers", count: held },
          { id: "alerts", label: "Alerts" },
          { id: "rules", label: "Rules" },
          { id: "blocklist", label: "Blocklist" },
          { id: "logins", label: "Sign-in attempts" },
        ]}
      />
      {tab === "queue" && <ReviewQueue state={queue} />}
      {tab === "alerts" && <Alerts />}
      {tab === "rules" && <Rules />}
      {tab === "blocklist" && <Blocklist />}
      {tab === "logins" && <Logins />}
    </>
  );
}
