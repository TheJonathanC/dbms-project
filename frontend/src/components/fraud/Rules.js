"use client";

import { useState } from "react";
import { api, useApi } from "../../lib/api";
import { sentence } from "../../lib/format";
import { Async, Button, Panel } from "../ui";

function Row({ rule, onSaved }) {
  const [draft, setDraft] = useState({ threshold: String(rule.threshold_value), action: rule.action, active: rule.is_active });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const dirty =
    Number(draft.threshold) !== Number(rule.threshold_value) || draft.action !== rule.action || draft.active !== rule.is_active;

  async function save() {
    setBusy(true);
    setError("");
    try {
      await api(`/fraud/rules/${rule.rule_id}`, {
        method: "PATCH",
        body: { threshold_value: Number(draft.threshold), action: draft.action, is_active: draft.active },
      });
      onSaved(`${rule.rule_name} saved. The next transfer is checked against the new values.`);
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }

  return (
    <tr>
      <td>
        {rule.rule_name}
        <span className="sub">{rule.description}</span>
        {error ? <span className="sub" style={{ color: "var(--bad)" }}>{error}</span> : null}
      </td>
      <td className="small muted">{sentence(rule.evaluation_metric)}</td>
      <td>
        <input
          className="input compact num"
          style={{ width: 110 }}
          type="number"
          min="0"
          step="any"
          aria-label={`Threshold for ${rule.rule_name}`}
          value={draft.threshold}
          onChange={(e) => setDraft({ ...draft, threshold: e.target.value })}
        />
      </td>
      <td>
        <select
          className="input compact"
          style={{ width: 120 }}
          aria-label={`Action for ${rule.rule_name}`}
          value={draft.action}
          onChange={(e) => setDraft({ ...draft, action: e.target.value })}
        >
          <option value="FLAG">Flag</option>
          <option value="QUARANTINE">Hold</option>
          <option value="BLOCK">Block</option>
        </select>
      </td>
      <td>
        <input
          className="switch"
          type="checkbox"
          aria-label={`${rule.rule_name} is active`}
          checked={draft.active}
          onChange={(e) => setDraft({ ...draft, active: e.target.checked })}
        />
      </td>
      <td className="right num">
        {rule.total_hits}
        {rule.false_positives ? <span className="sub">{rule.false_positives} false alarm{rule.false_positives === 1 ? "" : "s"}</span> : null}
      </td>
      <td>
        <Button small variant={dirty ? "primary" : ""} disabled={!dirty || busy} onClick={save}>Save</Button>
      </td>
    </tr>
  );
}

export default function Rules() {
  const state = useApi("/fraud/rules");
  const [saved, setSaved] = useState("");

  return (
    <div className="stack">
      {saved ? <div className="notice good" role="status">{saved}</div> : null}
      <Panel
        title="Fraud rules"
        note="Rules live in the database, so a change here applies to the very next transfer. Thresholds are an amount in rupees, or a count, depending on the check."
        flush
      >
        <Async state={state} what="Loading rules">
          {(rules) => (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Rule</th>
                    <th scope="col">Check</th>
                    <th scope="col">Threshold</th>
                    <th scope="col">Action</th>
                    <th scope="col">On</th>
                    <th scope="col" className="right">Times triggered</th>
                    <th scope="col"><span className="sr">Save</span></th>
                  </tr>
                </thead>
                <tbody>
                  {rules.map((r) => (
                    <Row
                      key={`${r.rule_id}-${r.threshold_value}-${r.action}-${r.is_active}`}
                      rule={r}
                      onSaved={(text) => {
                        setSaved(text);
                        state.reload();
                      }}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Async>
      </Panel>
    </div>
  );
}
