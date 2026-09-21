import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { pathToFileURL } from "node:url";

const MAX_OUTPUT = 65536;
const MAX_FILES = 256;
const reserved = new Set([".git", ".gitmodules", ".gitattributes", ".env", ".agent", ".qwen-home", ".uar-tools", ".netrc", ".npmrc", "id_rsa", "id_ed25519"]);
export class OperationError extends Error { constructor(code) { super(code); this.code = code; } }
const reject = (code = "workspace_rejected") => { throw new OperationError(code); };
const identifier = (value) => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(value);
function branch(value) {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/.test(value) || value.includes("..") || value === "HEAD"
    || value.split("/").some((p) => !p || p.startsWith(".") || p.endsWith(".") || p.endsWith(".lock"))) reject();
  return value;
}
export function projectPath(value) {
  if (typeof value !== "string" || value.length > 240 || value.split("/").some((p) => !/^[A-Za-z0-9_.-]{1,128}$/.test(p)
    || [".", ".."].includes(p) || reserved.has(p.toLowerCase()) || p.endsWith(".")
    || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(p))) reject();
  return value;
}
async function exists(target) { try { return await fs.lstat(target); } catch (error) { if (error.code === "ENOENT") return null; throw error; } }
async function safePath(root, relative) {
  let current = root;
  for (const part of relative.split("/")) {
    current = path.join(current, part);
    const info = await exists(current);
    if (info && (info.isSymbolicLink() || (!info.isDirectory() && (!info.isFile() || info.nlink !== 1)))) reject();
  }
  return current;
}
function cleanEnvironment(root) {
  const environment = {};
  for (const key of ["PATH", "Path", "SystemRoot", "SYSTEMROOT", "JAVA_HOME", "TEMP", "TMP", "PATHEXT"])
    if (process.env[key]) environment[key] = process.env[key];
  return { ...environment, HOME: root, USERPROFILE: root, LANG: "C.UTF-8", LC_ALL: "C.UTF-8",
    GIT_CONFIG_NOSYSTEM: "1", GIT_CONFIG_GLOBAL: process.platform === "win32" ? "NUL" : "/dev/null",
    GIT_TERMINAL_PROMPT: "0", GCM_INTERACTIVE: "never", GIT_ASKPASS: "", SSH_ASKPASS: "",
    GIT_AUTHOR_NAME: "Java Agent", GIT_AUTHOR_EMAIL: "java-agent@example.invalid",
    GIT_COMMITTER_NAME: "Java Agent", GIT_COMMITTER_EMAIL: "java-agent@example.invalid",
    GRADLE_USER_HOME: path.join(root, ".gradle"), MAVEN_CONFIG: path.join(root, ".m2") };
}
export function runBounded(argv, cwd, environment, timeoutMs = 120000) {
  return new Promise((resolve, rejectPromise) => {
    let output = "", size = 0, failure = null;
    const child = spawn(argv[0], argv.slice(1), { cwd, env: environment, shell: false, windowsHide: true,
      detached: process.platform !== "win32", stdio: ["ignore", "pipe", "pipe"] });
    function kill() {
      try { if (process.platform !== "win32" && child.pid) process.kill(-child.pid, "SIGKILL"); else child.kill("SIGKILL"); } catch { /* already exited */ }
    }
    const timer = setTimeout(() => { failure = "operation_timeout"; kill(); }, timeoutMs);
    const capture = (chunk) => { size += chunk.length; if (size > MAX_OUTPUT) { failure = "output_limit"; kill(); } else output += chunk.toString("utf8"); };
    child.stdout.on("data", capture); child.stderr.on("data", capture);
    child.on("error", () => { failure = "operation_failed"; });
    child.on("close", (status) => {
      clearTimeout(timer); kill();
      if (failure) rejectPromise(new OperationError(failure));
      else resolve({ success: status === 0, output, exit_code: status });
    });
  });
}

async function inventory(project) {
  const files = [];
  let bytes = 0;
  async function walk(relative = "") {
    const entries = await fs.readdir(path.join(project, relative), { withFileTypes: true });
    for (const entry of entries) {
      if ([".git", "target", "build", ".gradle"].includes(entry.name)) continue;
      const name = relative ? `${relative}/${entry.name}` : entry.name;
      projectPath(name);
      const target = await safePath(project, name);
      // Existing Gradle wrapper binary is executed only inside Agent; it is not
      // model input or a text-file proposal. Installed Gradle needs no wrapper.
      if (name === "gradle/wrapper/gradle-wrapper.jar") {
        const info = await fs.lstat(target);
        if (!info.isFile() || info.size > 1048576) reject();
        continue;
      }
      if (entry.isDirectory()) await walk(name);
      else {
        const info = await fs.lstat(target);
        if (!info.isFile() || info.size > 32768 || files.length >= MAX_FILES) reject();
        bytes += info.size;
        if (bytes > 131072) reject("output_limit");
        const content = await fs.readFile(target, "utf8");
        if (content.includes("\0") || content.includes("\uFFFD")) reject();
        files.push({ path: name, content });
      }
    }
  }
  await walk();
  return files.sort((a, b) => a.path.localeCompare(b.path));
}

async function gitSafety(project) {
  const directory = await safePath(project, ".git");
  if (!(await exists(directory))?.isDirectory()) reject();
  let entries = 0;
  async function inspect(relative = "") {
    for (const entry of await fs.readdir(path.join(directory, relative), { withFileTypes: true })) {
      if (++entries > 8192) reject();
      const name = relative ? `${relative}/${entry.name}` : entry.name;
      await safePath(directory, name);
      if (entry.isDirectory()) await inspect(name);
    }
  }
  await inspect();
  for (const name of ["config", "index", "HEAD", "objects", "refs", "objects/info", "objects/info/alternates"])
    await safePath(directory, name);
  if (await exists(path.join(directory, "objects/info/alternates")) || await exists(path.join(directory, "config.worktree"))) reject();
  const config = await fs.readFile(path.join(directory, "config"), "utf8");
  let section = "";
  for (const raw of config.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || line.startsWith(";")) continue;
    if (line.startsWith("[")) {
      if (line === "[core]") section = "core";
      else if (line === '[remote "origin"]') section = "remote";
      else if (/^\[branch "[A-Za-z0-9._/-]+"\]$/.test(line)) section = "branch";
      else reject();
    } else {
      const key = line.match(/^([A-Za-z]+)\s*=\s*(.*)$/)?.[1]?.toLowerCase();
      const allowed = { core: ["repositoryformatversion", "filemode", "bare", "logallrefupdates", "ignorecase", "symlinks", "precomposeunicode"],
        remote: ["url", "fetch"], branch: ["remote", "merge"] };
      if (!allowed[section]?.includes(key)) reject();
    }
  }
}

function remoteLocation(value, root, policy) {
  if (typeof value !== "string" || value.includes("\0") || value.length > 2048) reject("publication_rejected");
  if (policy.localTestRoot) {
    const relative = path.relative(path.resolve(policy.localTestRoot), path.resolve(value));
    if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) reject("publication_rejected");
    return path.resolve(value);
  }
  let url;
  try { url = new URL(value); } catch { reject("publication_rejected"); }
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash || url.port && url.port !== "443"
    || !policy.allowedHosts?.includes(url.hostname) || /[\s\\]/.test(value)) reject("publication_rejected");
  return value;
}

export async function execute(request, { root = "/workspace", allowedHosts = [], localTestRoot = null, timeoutMs = 120000, run = runBounded } = {}) {
  if (!identifier(request.task_id)) reject();
  const selectedBranch = branch(request.branch || "main");
  const rootInfo = await fs.lstat(root);
  if (!rootInfo.isDirectory() || rootInfo.isSymbolicLink()) reject();
  root = await fs.realpath(root);
  const parent = await safePath(root, "projects");
  await fs.mkdir(parent, { recursive: true });
  const project = await safePath(root, `projects/${request.task_id}`);
  const environment = cleanEnvironment(root);
  const gitArgs = ["git", "-c", "core.hooksPath=/usr/local/share/uar/empty-hooks", "-c", "core.fsmonitor=false",
    "-c", "credential.helper=", "-c", "http.followRedirects=false", "-c", "commit.gpgSign=false",
    "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", `protocol.file.allow=${localTestRoot ? "always" : "never"}`,
    "-c", "init.templateDir=", "-c", "core.autocrlf=false"];
  const git = async (...args) => {
    const result = await run([...gitArgs, ...args], project, environment, timeoutMs);
    if (!result.success) reject("operation_failed");
    return result;
  };
  if (request.operation === "prepare") { await fs.mkdir(project, { recursive: true }); return { success: true }; }
  if (!(await exists(project))?.isDirectory()) reject();
  if (request.operation === "write") {
    if (!Array.isArray(request.files) || !request.files.length || request.files.length > 64) reject();
    const names = new Set();
    for (const file of request.files) {
      const name = projectPath(file.path);
      if (names.has(name.toLowerCase()) || typeof file.content !== "string" || file.content.includes("\0") || Buffer.byteLength(file.content) > 32768) reject();
      names.add(name.toLowerCase());
      await safePath(project, name);
    }
    for (const file of request.files) {
      const target = await safePath(project, file.path);
      await fs.mkdir(path.dirname(target), { recursive: true });
      await fs.writeFile(target, file.content, { encoding: "utf8", flag: "w" });
    }
    return { success: true };
  }
  if (request.operation === "inventory") return { success: true, files: await inventory(project) };
  if (request.operation === "init" || request.operation === "clone") {
    if ((await fs.readdir(project)).length) reject();
    if (request.operation === "init") await git("init", `--initial-branch=${selectedBranch}`, ".");
    else {
      const remote = remoteLocation(request.remote, root, { allowedHosts, localTestRoot });
      if (localTestRoot && await fs.realpath(remote) !== path.resolve(remote)) reject();
      await git("clone", "--no-local", "--", remote, ".");
      const current = await git("symbolic-ref", "--short", "HEAD");
      if (current.output.trim() !== selectedBranch) await git("checkout", "-b", selectedBranch);
    }
    await gitSafety(project);
    return { success: true };
  }
  await gitSafety(project);
  switch (request.operation) {
    case "test":
    case "package": {
      let argv, check;
      if (request.build_system === "maven") {
        if (!(await exists(await safePath(project, "pom.xml")))?.isFile()) reject();
        const goal = request.operation === "test" ? "test" : "package";
        argv = ["mvn", "-B", "-ntp", `-Dmaven.repo.local=${path.join(root, ".m2/repository")}`, goal];
        check = `mvn ${goal}`;
      } else if (request.build_system === "gradle") {
        const wrapper = await exists(await safePath(project, "gradlew"));
        const goal = request.operation === "test" ? "test" : "build";
        argv = wrapper ? ["bash", "./gradlew"] : ["gradle"];
        argv.push("--no-daemon", "--console=plain", goal);
        check = `${wrapper ? "./gradlew" : "gradle"} ${goal}`;
      } else reject();
      await inventory(project);
      const result = await run(argv, project, environment, timeoutMs);
      await gitSafety(project);
      return { ...result, check };
    }
    case "status": return git("status", "--porcelain=v1", "--untracked-files=all");
    case "diff": return git("diff", "--cached", "--no-ext-diff", "--no-textconv", "--");
    case "add": {
      if (!Array.isArray(request.paths) || !request.paths.length || request.paths.length > MAX_FILES) reject();
      for (const name of request.paths) await safePath(project, projectPath(name));
      return git("add", "--", ...request.paths);
    }
    case "commit": {
      if ((await git("symbolic-ref", "--short", "HEAD")).output.trim() !== selectedBranch) reject();
      await git("commit", "--no-verify", "-m", `Implement development task ${request.task_id}`);
      return { success: true, commit_id: (await git("rev-parse", "HEAD")).output.trim() };
    }
    case "push": {
      if (request.publish_authorized !== true || (await git("symbolic-ref", "--short", "HEAD")).output.trim() !== selectedBranch) reject("publication_rejected");
      const remote = remoteLocation(request.remote, root, { allowedHosts, localTestRoot });
      await git("push", "--porcelain", "--", remote, `HEAD:refs/heads/${selectedBranch}`);
      return { success: true };
    }
    default: reject();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    if (process.argv.length !== 3 || Buffer.byteLength(process.argv[2]) > 131072) reject();
    const result = await execute(JSON.parse(process.argv[2]), { root: process.env.UAR_WORKSPACE || "/workspace",
      allowedHosts: (process.env.UAR_GIT_ALLOWED_HOSTS || "").split(",").filter(Boolean) });
    console.log(JSON.stringify(result));
  } catch (error) {
    console.log(JSON.stringify({ success: false, error: error instanceof OperationError ? error.code : "operation_failed" }));
    process.exitCode = 1;
  }
}
