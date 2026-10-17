/** Geometry for the onset chart: pure so it can be tested without a DOM. */

export type Point = { t: number; v: number | null };
export type Geometry = { line: string; area: string; x: (t: number) => number; y: (v: number) => number; max: number };

/** "Nice" upper bound for the y axis (1, 2, 2.5, 5 x 10^n). */
export function niceMax(v: number): number {
  if (!(v > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * p) return m * p;
  return 10 * p;
}

/** SVG paths over [t0, t1]; gaps (null values) break the line. */
export function geometry(points: Point[], t0: number, t1: number, w: number, h: number, pad = 4): Geometry {
  const max = niceMax(Math.max(0, ...points.map((p) => p.v ?? 0)));
  const x = (t: number) => ((t - t0) / Math.max(1, t1 - t0)) * w;
  const y = (v: number) => h - pad - (v / max) * (h - 2 * pad);
  let line = "";
  let area = "";
  let run: [number, number][] = [];
  const flush = () => {
    if (!run.length) return;
    line += run.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)} ${py.toFixed(1)}`).join(" ") + " ";
    area += `M${run[0][0].toFixed(1)} ${h} ` + run.map(([px, py]) => `L${px.toFixed(1)} ${py.toFixed(1)}`).join(" ") + ` L${run[run.length - 1][0].toFixed(1)} ${h} Z `;
    run = [];
  };
  for (const p of points) {
    if (p.v === null) flush();
    else run.push([x(p.t), y(p.v)]);
  }
  flush();
  return { line: line.trim(), area: area.trim(), x, y, max };
}
