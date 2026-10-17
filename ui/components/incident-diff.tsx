"use client";

import { useTheme } from "next-themes";
import { Highlight, themes } from "prism-react-renderer";
import { useMemo } from "react";
import { readableColor } from "@/lib/contrast";
import { languageOf, parseUnifiedDiff, type DiffHunk } from "@/lib/diff";
import { cn } from "@/lib/utils";

const ROW = { add: "bg-green/12", del: "bg-red/12", ctx: "" } as const;
const SIGN = { add: "+", del: "−", ctx: " " } as const;
// Row backgrounds (panel-2 with 12% red / green from globals.css, plus the plain panel) used to
// keep token colours at WCAG AA on every row; see lib/contrast.ts.
const ROW_BG = {
  light: ["#f7e7e6", "#e4efe9", "#ffffff"],
  dark: ["#332326", "#1c2e28", "#191a1f"],
} as const;

function Hunk({ hunk, language, dark }: { hunk: DiffHunk; language: string; dark: boolean }) {
  const code = hunk.lines.map((l) => l.text).join("\n");
  return (
    <Highlight code={code} language={language} theme={dark ? themes.vsDark : themes.github}>
      {({ tokens, getTokenProps }) => (
        <tbody>
          <tr>
            <td colSpan={4} className="bg-accent/8 px-3 py-1 font-mono text-[11px] text-ink-3">
              {hunk.header}
            </td>
          </tr>
          {hunk.lines.map((l, i) => (
            <tr key={i} className={ROW[l.type]} data-line={l.type}>
              <td className="num w-10 select-none px-2 text-right text-ink-2">{l.oldNo ?? ""}</td>
              <td className="num w-10 select-none px-2 text-right text-ink-2">{l.newNo ?? ""}</td>
              <td className={cn("w-4 select-none text-center", l.type === "add" ? "text-green" : l.type === "del" ? "text-red" : "text-ink-4")} aria-label={l.type === "add" ? "added" : l.type === "del" ? "removed" : undefined}>
                {SIGN[l.type]}
              </td>
              <td className="whitespace-pre pr-4">
                {(tokens[i] ?? []).map((t, k) => {
                  const { style, ...rest } = getTokenProps({ token: t });
                  const bgs = dark ? ROW_BG.dark : ROW_BG.light;
                  return <span key={k} {...rest} style={{ color: readableColor(style?.color, bgs) }} />;
                })}
              </td>
            </tr>
          ))}
        </tbody>
      )}
    </Highlight>
  );
}

/** Unified diff with per-file headers, line numbers and syntax highlighting. */
export function DiffViewer({ diff }: { diff: string }) {
  const { resolvedTheme } = useTheme();
  const files = useMemo(() => parseUnifiedDiff(diff), [diff]);
  if (!files.length) return <pre className="overflow-x-auto rounded-md border border-line bg-panel-2 p-3 font-mono text-[11.5px]">{diff}</pre>;
  return (
    <div className="flex flex-col gap-3" aria-label="Fix diff">
      {files.map((f) => (
        <div key={`${f.oldPath}->${f.newPath}`} className="overflow-hidden rounded-lg border border-line">
          <div className="flex items-center gap-2 border-b border-line bg-panel-2 px-3 py-1.5 text-[12px]">
            <code className="font-mono text-ink">{f.newPath ?? f.oldPath}</code>
            <span className="num ml-auto text-green">+{f.added}</span>
            <span className="num text-red">−{f.removed}</span>
          </div>
          <div className="overflow-x-auto" data-lenis-prevent>
            <table className="w-full border-collapse font-mono text-[11.5px] leading-[1.6]">
              {f.hunks.map((h, i) => (
                <Hunk key={i} hunk={h} language={languageOf(f.newPath ?? f.oldPath)} dark={resolvedTheme !== "light"} />
              ))}
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}
