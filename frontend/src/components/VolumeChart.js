"use client";

// Transfers per hour for the last 24 hours, stacked by outcome. Plain SVG, no chart library.

const HOUR = 3_600_000;
const PARTS = [
  { key: "success", name: "Completed", color: "#1d4a9e" },
  { key: "quarantined", name: "On hold", color: "#c98a1b" },
  { key: "rejected", name: "Rejected", color: "#b0261c" },
  { key: "failed", name: "Failed", color: "#8a97a8" },
];

function buckets(hourly) {
  const byHour = new Map(hourly.map((h) => [Math.floor(new Date(h.hour).getTime() / HOUR), h]));
  const now = Math.floor(Date.now() / HOUR);
  return Array.from({ length: 24 }, (_, i) => {
    const slot = now - 23 + i;
    return { at: slot * HOUR, row: byHour.get(slot) };
  });
}

export function ChartLegend() {
  return (
    <div className="legend">
      {PARTS.map((p) => (
        <span key={p.key}>
          <i style={{ background: p.color }} />
          {p.name}
        </span>
      ))}
    </div>
  );
}

export default function VolumeChart({ hourly }) {
  const data = buckets(hourly || []);
  const max = Math.max(1, ...data.map((d) => d.row?.total || 0));
  const W = 720;
  const H = 176;
  const top = 8;
  const base = H - 24;
  const slot = W / 24;
  const bar = slot - 8;
  const total = data.reduce((sum, d) => sum + (d.row?.total || 0), 0);
  const when = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", hour: "numeric", minute: "2-digit" });

  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${total} transfers in the last 24 hours`}>
      <line className="axis" x1="0" x2={W} y1={base} y2={base} />
      {data.map((d, i) => {
        let y = base;
        return (
          <g key={d.at} transform={`translate(${i * slot + 4},0)`}>
            <title>{`${when.format(new Date(d.at))}: ${d.row?.total || 0} transfers`}</title>
            {PARTS.map((p) => {
              const n = d.row?.[p.key] || 0;
              if (!n) return null;
              const h = ((base - top) * n) / max;
              y -= h;
              return <rect key={p.key} x="0" y={y} width={bar} height={h} fill={p.color} rx="1.5" />;
            })}
          </g>
        );
      })}
      <text x="4" y={H - 6}>24 h ago</text>
      <text x={W / 4} y={H - 6} textAnchor="middle">18 h</text>
      <text x={W / 2} y={H - 6} textAnchor="middle">12 h</text>
      <text x={(3 * W) / 4} y={H - 6} textAnchor="middle">6 h</text>
      <text x={W - 4} y={H - 6} textAnchor="end">now</text>
      {total === 0 ? (
        <text x={W / 2} y={base / 2} textAnchor="middle" style={{ fontSize: 13 }}>No transfers in the last 24 hours</text>
      ) : (
        <text x="4" y={top + 8}>{max}</text>
      )}
    </svg>
  );
}
