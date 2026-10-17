// WCAG 2.x contrast helpers. Syntax-highlight themes pick colours for a neutral background; on the
// diff's tinted rows some fall below AA (4.5:1), so those tokens fall back to the text colour.

const hex = (c: string): [number, number, number] | null => {
  const rgb = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i.exec(c.trim());
  if (rgb) return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])];
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(c.trim());
  if (!m) return null;
  const h = m[1].length === 3 ? [...m[1]].map((x) => x + x).join("") : m[1];
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
};

const luminance = ([r, g, b]: [number, number, number]) => {
  const lin = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
};

/** Contrast ratio of two #rgb/#rrggbb/rgb() colours, or null when either can't be parsed. */
export function contrastRatio(a: string, b: string): number | null {
  const x = hex(a);
  const y = hex(b);
  if (!x || !y) return null;
  const [hi, lo] = [luminance(x), luminance(y)].sort((p, q) => q - p);
  return (hi + 0.05) / (lo + 0.05);
}

/** `color` if it reaches `min` contrast on every background (or can't be checked), else
 * undefined so the element inherits the regular text colour. */
export function readableColor(
  color: string | undefined,
  backgrounds: string | readonly string[],
  min = 4.5,
): string | undefined {
  if (!color) return undefined;
  const all = typeof backgrounds === "string" ? [backgrounds] : backgrounds;
  const ok = all.every((bg) => {
    const r = contrastRatio(color, bg);
    return r === null || r >= min;
  });
  return ok ? color : undefined;
}
