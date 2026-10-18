// Next.js with the mock API (NEXT_PUBLIC_API_MOCK=1) on any OS; POSIX inline env
// (`VAR=1 next dev`) does not work in Windows shells.
//   node scripts/dev-mock.mjs -p 3022          next dev
//   node scripts/dev-mock.mjs build            next build (mock bundle)
//   node scripts/dev-mock.mjs start -p 3022    next start
import { spawn } from "node:child_process";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const next = require.resolve("next/dist/bin/next");
const args = process.argv.slice(2);
const cmd = ["dev", "build", "start"].includes(args[0]) ? args : ["dev", ...args];
const child = spawn(process.execPath, [next, ...cmd], {
  stdio: "inherit",
  // the mock bundle inlines NEXT_PUBLIC_API_MOCK, so it must never share .next with the real app
  env: { ...process.env, NEXT_PUBLIC_API_MOCK: "1", NEXT_DIST_DIR: ".next-mock" },
});
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => child.kill(sig));
child.on("exit", (code, signal) => process.exit(signal ? 1 : (code ?? 0)));
