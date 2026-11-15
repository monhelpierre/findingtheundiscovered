// Copy the pipeline's export (data/export/, or ANALOG_EXPORT_DIR) into public/data/,
// where the app loads it as static files. Commit public/data/ so Vercel can serve it.
// If the export folder doesn't exist (e.g. on Vercel), keep the copy that is already committed.
import { cpSync, existsSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = process.env.ANALOG_EXPORT_DIR ?? path.resolve(web, "..", "data", "export");
const dst = path.join(web, "public", "data");

if (!existsSync(path.join(src, "index.json"))) {
  console.log(`sync-data: no export in ${src}; keeping ${dst} as it is.`);
  process.exit(0);
}
rmSync(dst, { recursive: true, force: true });
cpSync(src, dst, { recursive: true });
console.log(`sync-data: copied ${src} -> ${dst}`);
