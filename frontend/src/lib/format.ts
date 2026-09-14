export function fmt(n: number): string {
  return n.toString().padStart(2, "0");
}

export function toSec(t: string): number {
  const p = t.split(":").map(Number);
  if (p.length >= 3) return p[0] * 3600 + p[1] * 60 + p[2];
  if (p.length === 2) return p[0] * 60 + p[1]; // "MM:SS"
  return p[0] || 0;
}

export function fmtClock(sec: number): string {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}
