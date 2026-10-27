"use client";

import { label, tone } from "../lib/format";

export function PageHeader({ title, lede, children }) {
  return (
    <header className="page-head">
      <div>
        <h1 className="page-title">{title}</h1>
        {lede ? <p className="page-lede">{lede}</p> : null}
      </div>
      {children ? <div className="row">{children}</div> : null}
    </header>
  );
}

export function Panel({ title, note, action, flush, children }) {
  return (
    <section className="panel">
      {title || action ? (
        <div className="panel-head">
          <div>
            {title ? <h2 className="panel-title">{title}</h2> : null}
            {note ? <p className="panel-note">{note}</p> : null}
          </div>
          {action}
        </div>
      ) : null}
      <div className={flush ? "panel-body flush" : "panel-body"}>{children}</div>
    </section>
  );
}

export function Badge({ children, tone: t = "" }) {
  return <span className={`badge ${t}`.trim()}>{children}</span>;
}

// A badge coloured and worded from a database status such as QUARANTINED or HEALTHY.
export function Status({ value }) {
  if (!value) return null;
  return <Badge tone={tone(value)}>{label(value)}</Badge>;
}

export function Button({ variant = "", small, children, ...props }) {
  const cls = ["btn", variant, small ? "small" : ""].filter(Boolean).join(" ");
  return (
    <button type="button" className={cls} {...props}>
      {children}
    </button>
  );
}

// columns: [{ key, label, align: "right", render: (row) => node }]
export function Table({ columns, rows, rowKey, empty = "Nothing to show yet." }) {
  if (!rows || rows.length === 0) return <Empty title={empty} />;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={c.align === "right" ? "right" : ""}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={rowKey ? row[rowKey] : i}>
              {columns.map((c) => (
                <td key={c.key} className={[c.align === "right" ? "right num" : "", c.nowrap ? "nowrap" : ""].join(" ").trim()}>
                  {c.render ? c.render(row) : row[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Figures({ items }) {
  return (
    <div className="figures">
      {items.map((f) => (
        <div className="figure" key={f.label}>
          <div className="figure-value">{f.value}</div>
          <div className="figure-label">{f.label}</div>
          {f.sub ? <div className="figure-sub">{f.sub}</div> : null}
        </div>
      ))}
    </div>
  );
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} type="button" role="tab" className="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}>
          {t.label}
          {t.count ? <span className="count">{t.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function Field({ label: text, hint, wide, children }) {
  return (
    <div className={wide ? "field wide" : "field"}>
      <span className="field-label">{text}</span>
      {children}
      {hint ? <span className="hint">{hint}</span> : null}
    </div>
  );
}

export function Empty({ title, children }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children}
    </div>
  );
}

export function Loading({ what = "Loading" }) {
  return (
    <div className="loading" role="status" aria-busy="true">
      {what}...
    </div>
  );
}

export function ErrorNote({ error, onRetry }) {
  return (
    <div className="notice bad" role="alert">
      {error?.message || "Something went wrong."}{" "}
      {onRetry ? (
        <button type="button" className="linklike" style={{ color: "inherit" }} onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

// Handles the three states every page has: still loading, failed, loaded.
export function Async({ state, what, children }) {
  if (state.error && !state.data) return <ErrorNote error={state.error} onRetry={state.reload} />;
  if (!state.data) return <Loading what={what} />;
  return children(state.data);
}

// Small bar for a 0..1 value.
export function Meter({ value, tone: t = "" }) {
  return (
    <span className={`meter ${t}`.trim()} aria-hidden="true">
      <span style={{ width: `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%` }} />
    </span>
  );
}
