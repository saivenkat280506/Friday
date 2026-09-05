/**
 * Clone Chrome Default profile (cookies/logins) into a dedicated FRIDAY
 * Chrome user-data dir so automation never fights your open Chrome lock.
 *
 * Source: ~/Library/Application Support/Google/Chrome/Default
 * Dest:   browser-automation/chrome-profile-data/Default
 */
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const DEST_ROOT = path.join(ROOT, "chrome-profile-data");
const DEST_DEFAULT = path.join(DEST_ROOT, "Default");
const SRC_USER_DATA =
  process.env.CHROME_USER_DATA ||
  path.join(os.homedir(), "Library", "Application Support", "Google", "Chrome");
const SRC_PROFILE = process.env.CHROME_PROFILE_DIRECTORY || "Default";
const SRC_DEFAULT = path.join(SRC_USER_DATA, SRC_PROFILE);

const EXCLUDE_DIRS = [
  "Cache",
  "Code Cache",
  "GPUCache",
  "Service Worker",
  "DawnCache",
  "GrShaderCache",
  "ShaderCache",
  "optimization_guide_model_store",
  "JumpListIconsMostVisited",
  "JumpListIconsRecentClosed",
  "blob_storage",
  "File System",
];

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function writeLocalState() {
  // Minimal Local State so Chrome accepts this user-data dir
  const localState = {
    profile: {
      info_cache: {
        Default: {
          name: "FRIDAY",
          is_using_default_name: false,
        },
      },
      last_used: "Default",
    },
  };
  fs.writeFileSync(path.join(DEST_ROOT, "Local State"), JSON.stringify(localState));
}

function main() {
  if (!fs.existsSync(SRC_DEFAULT)) {
    console.error("[sync] Source profile missing:", SRC_DEFAULT);
    process.exit(1);
  }
  ensureDir(DEST_ROOT);
  ensureDir(DEST_DEFAULT);
  writeLocalState();

  console.log("[sync] Source:", SRC_DEFAULT);
  console.log("[sync] Dest:  ", DEST_DEFAULT);

  const exclude = EXCLUDE_DIRS.flatMap((d) => ["--exclude", d]);
  const r = spawnSync(
    "rsync",
    ["-a", "--delete", ...exclude, `${SRC_DEFAULT}/`, `${DEST_DEFAULT}/`],
    { encoding: "utf8", shell: false }
  );
  if ((r.status ?? 1) !== 0) {
    console.error("[sync] rsync failed", r.status, r.stdout, r.stderr);
    process.exit(1);
  }
  console.log("[sync] Profile synced OK via rsync");
  console.log("[sync] Use CHROME_USER_DATA=" + DEST_ROOT);
  console.log("[sync] Use CHROME_PROFILE_DIRECTORY=Default");
}

main();
