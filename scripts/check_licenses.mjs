#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const lockPath = path.join(root, "dashboard-web", "package-lock.json");
const lock = JSON.parse(fs.readFileSync(lockPath, "utf8"));
const forbidden = /(^|[^L])A?GPL(?:-|$)/i;
const errors = [];
let checked = 0;

for (const [location, metadata] of Object.entries(lock.packages ?? {})) {
  if (!location.includes("node_modules/") || metadata.dev === true) continue;
  const name = location.slice(location.lastIndexOf("node_modules/") + 13);
  const license = typeof metadata.license === "string" ? metadata.license.trim() : "";
  checked += 1;
  if (!license) errors.push(`${name} 缺少许可证元数据`);
  else if (forbidden.test(license)) {
    errors.push(`${name} 使用禁止的强 copyleft 许可证 ${license}`);
  }
}

if (errors.length) {
  for (const error of errors) console.error(`错误：${error}`);
  process.exit(1);
}
console.log(`npm 许可证检查通过：已检查 ${checked} 个锁定的生产依赖包，无 GPL/AGPL 强 copyleft。`);
