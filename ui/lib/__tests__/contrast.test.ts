import { describe, expect, it } from "vitest";
import { contrastRatio, readableColor } from "../contrast";

describe("contrast", () => {
  it("computes WCAG ratios", () => {
    expect(contrastRatio("#000", "#fff")).toBeCloseTo(21, 0);
    expect(contrastRatio("#ffffff", "#ffffff")).toBeCloseTo(1, 5);
    expect(contrastRatio("rgb(0, 0, 0)", "#fff")).toBeCloseTo(21, 0); // prism vsDark uses rgb()
    expect(contrastRatio("hsl(0 0% 0%)", "#fff")).toBeNull();
  });

  it("drops token colours below AA on the diff row background", () => {
    // prism github theme constant (#36acaa) on a red-tinted light row: below 4.5
    expect(readableColor("#36acaa", "#f6e4e1")).toBeUndefined();
    // dark, readable token colours are kept; unparseable colours pass through
    expect(readableColor("#24292e", "#f6e4e1")).toBe("#24292e");
    expect(readableColor("var(--x)", "#f6e4e1")).toBe("var(--x)");
    expect(readableColor(undefined, "#fff")).toBeUndefined();
    // must pass on every row background (red-tinted, green-tinted, plain)
    expect(readableColor("#24292e", ["#f7e7e6", "#e4efe9", "#ffffff"])).toBe("#24292e");
    expect(readableColor("#6a737d", ["#f7e7e6", "#e4efe9", "#ffffff"])).toBeUndefined();
  });
});
