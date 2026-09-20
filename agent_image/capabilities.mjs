import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";

const probes = Object.freeze({
  java: ["-version"], javac: ["-version"], git: ["--version"],
  mvn: ["-version"], gradle: ["--version"], bash: ["--version"],
  curl: ["--version"], unzip: ["-v"], qwen: ["--version"], node: ["--version"],
});

export function capabilities(run = spawnSync) {
  const tools = {};
  for (const [name, args] of Object.entries(probes)) {
    const result = run(name, args, { encoding: "utf8", timeout: 30000, maxBuffer: 65536, shell: false });
    const text = `${result.stdout || ""}\n${result.stderr || ""}`;
    const pattern = name === "java" ? /(?:openjdk|java) version "(\d+)[^"]*"/
      : name === "javac" ? /javac (\d+)/ : null;
    const major = pattern ? Number(text.match(pattern)?.[1] || 0) : null;
    tools[name] = { available: !result.error && result.status === 0 && (!pattern || major === 21), major };
  }
  return { schema_version: 1, ready: Object.values(tools).every((tool) => tool.available), tools };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const result = capabilities();
  console.log(JSON.stringify(result));
  process.exitCode = result.ready ? 0 : 1;
}
