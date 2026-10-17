/** Unified diff (git format) -> files, hunks and numbered lines for the diff viewer. */

export type DiffLine = { type: "add" | "del" | "ctx"; text: string; oldNo: number | null; newNo: number | null };
export type DiffHunk = { header: string; lines: DiffLine[] };
export type DiffFile = { oldPath: string | null; newPath: string | null; hunks: DiffHunk[]; added: number; removed: number };

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/;
const strip = (p: string) => (p === "/dev/null" ? null : p.replace(/^[ab]\//, ""));

export function parseUnifiedDiff(text: string): DiffFile[] {
  const files: DiffFile[] = [];
  // `as` keeps TS from narrowing these to null: `begin` assigns them inside a closure
  let file = null as DiffFile | null;
  let hunk = null as DiffHunk | null;
  let oldNo = 0;
  let newNo = 0;
  const begin = () => {
    file = { oldPath: null, newPath: null, hunks: [], added: 0, removed: 0 };
    files.push(file);
    hunk = null;
  };
  for (const raw of text.replace(/\r\n/g, "\n").split("\n")) {
    if (raw.startsWith("diff --git ")) {
      begin();
      const m = /^diff --git a\/(.+) b\/(.+)$/.exec(raw);
      if (m) [file!.oldPath, file!.newPath] = [m[1], m[2]];
      continue;
    }
    if (raw.startsWith("--- ") && (!hunk || !file)) {
      if (!file) begin();
      file!.oldPath = strip(raw.slice(4).trim());
      continue;
    }
    if (raw.startsWith("+++ ") && file && !hunk) {
      file.newPath = strip(raw.slice(4).trim());
      continue;
    }
    const m = HUNK.exec(raw);
    if (m) {
      if (!file) begin();
      hunk = { header: raw, lines: [] };
      file!.hunks.push(hunk);
      oldNo = Number(m[1]);
      newNo = Number(m[2]);
      continue;
    }
    if (!hunk || !file) continue; // index/mode lines between the header and the first hunk
    const f: DiffFile = file;
    const h: DiffHunk = hunk;
    if (raw.startsWith("+")) {
      h.lines.push({ type: "add", text: raw.slice(1), oldNo: null, newNo: newNo++ });
      f.added++;
    } else if (raw.startsWith("-")) {
      h.lines.push({ type: "del", text: raw.slice(1), oldNo: oldNo++, newNo: null });
      f.removed++;
    } else if (raw.startsWith(" ")) {
      h.lines.push({ type: "ctx", text: raw.slice(1), oldNo: oldNo++, newNo: newNo++ });
    }
    // anything else ("\ No newline at end of file", the text's trailing newline) is not a line
  }
  return files;
}

const LANG: Record<string, string> = { js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "jsx", ts: "typescript", tsx: "tsx", py: "python", json: "json", go: "go", rb: "ruby", yml: "yaml", yaml: "yaml", css: "css", md: "markdown" };

export function languageOf(path: string | null): string {
  const ext = (path ?? "").split(".").pop()?.toLowerCase() ?? "";
  return LANG[ext] ?? "clike";
}
