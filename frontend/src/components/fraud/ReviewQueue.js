"use client";

import { useState } from "react";
import { api } from "../../lib/api";
import { money } from "../../lib/format";
import { Async, Button, Empty, Panel } from "../ui";

function Case({ item, onDone }) {
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function decide(approve) {
    setBusy(true);
    setError("");
    try {
      await api(`/fraud/review/${item.transaction_id}`, { method: "POST", body: { approve, notes: notes.trim() || null } });
      onDone(`${money(item.amount)} from ${item.sender_name} to ${item.receiver_name} was ${approve ? "released" : "rejected"}.`);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  return (
    <div className="case">
      <div className="case-top">
        <div>
          <div className="list-title">{item.sender_name} to {item.receiver_name}</div>
          <div className="list-meta">
            Waiting {Math.round(item.waiting_minutes)} min. Triggered: {item.rules_triggered}
          </div>
        </div>
        <div className="case-amount">{money(item.amount)}</div>
      </div>
      <div className="case-actions">
        <input
          className="input compact"
          placeholder="Note for the record (optional)"
          aria-label="Note for the record"
          value={notes}
          maxLength={255}
          onChange={(e) => setNotes(e.target.value)}
        />
        <Button variant="primary" disabled={busy} onClick={() => decide(true)}>Release transfer</Button>
        <Button variant="danger" disabled={busy} onClick={() => decide(false)}>Reject transfer</Button>
      </div>
      {error ? <div className="notice bad" role="alert">{error}</div> : null}
    </div>
  );
}

export default function ReviewQueue({ state }) {
  const [done, setDone] = useState("");

  return (
    <div className="stack">
      {done ? <div className="notice good" role="status">{done}</div> : null}
      <Panel
        title="Waiting for a decision"
        note="Releasing moves the money (the balance is checked again first). Rejecting closes the transfer. Both are recorded against the alerts."
        flush
      >
        <Async state={state} what="Loading held transfers">
          {(rows) =>
            rows.length === 0 ? (
              <Empty title="Nothing is on hold">Transfers that trip a hold rule will appear here.</Empty>
            ) : (
              rows.map((item) => (
                <Case
                  key={item.transaction_id}
                  item={item}
                  onDone={(text) => {
                    setDone(text);
                    state.reload();
                  }}
                />
              ))
            )
          }
        </Async>
      </Panel>
    </div>
  );
}
