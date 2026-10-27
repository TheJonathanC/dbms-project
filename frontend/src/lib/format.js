// Formatting helpers. All times are shown in Indian Standard Time.

const TZ = "Asia/Kolkata";

export function money(n) {
  if (n === null || n === undefined) return "-";
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2, minimumFractionDigits: 0 }).format(Number(n));
}

// 16,07,000 -> "₹16.1 lakh"
export function moneyShort(n) {
  const v = Number(n || 0);
  if (v >= 1e7) return `₹${(v / 1e7).toFixed(2)} crore`;
  if (v >= 1e5) return `₹${(v / 1e5).toFixed(1)} lakh`;
  return money(v);
}

export function clock(iso) {
  if (!iso) return "-";
  return new Intl.DateTimeFormat("en-IN", { timeZone: TZ, day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }).format(new Date(iso));
}

export function ago(iso) {
  if (!iso) return "-";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

// "INSUFFICIENT_FUNDS" -> "Insufficient funds"
export function sentence(value) {
  if (!value) return "";
  const s = String(value).replace(/_/g, " ").toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// Words people use instead of the database codes.
const LABELS = {
  SUCCESS: "Completed",
  QUARANTINED: "On hold",
  REJECTED: "Rejected",
  FAILED: "Failed",
  PENDING: "Pending",
  QUARANTINE: "Hold",
  BLOCK: "Block",
  FLAG: "Flag",
  NOT_READY: "Not ready",
  FALSE_POSITIVE: "False alarm",
};
export function label(value) {
  return LABELS[value] || sentence(value);
}

export function reason(value) {
  if (!value) return "";
  if (value.startsWith("FRAUD: ")) return `Fraud rule: ${value.slice(7)}`;
  return sentence(value);
}

const TONES = {
  good: ["SUCCESS", "HEALTHY", "ACTIVE", "SENT", "RESOLVED", "CONFIRMED", "OK"],
  warn: ["QUARANTINED", "QUARANTINE", "PENDING", "PROCESSING", "DEGRADED", "FLAGGED", "FLAG", "OPEN", "LIMITED", "MEDIUM", "REPLAYING"],
  bad: ["REJECTED", "FAILED", "DOWN", "FROZEN", "DEAD", "BLOCK", "NOT_READY", "ABANDONED", "HIGH"],
  info: ["FALSE_POSITIVE"],
};
export function tone(value) {
  for (const [name, values] of Object.entries(TONES)) if (values.includes(value)) return name;
  return "";
}

export function newKey() {
  return typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `k-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
