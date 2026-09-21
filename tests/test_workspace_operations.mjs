import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import test from "node:test";
import { spawnSync } from "node:child_process";
import { execute, projectPath, runBounded } from "../agent_image/workspace-operations.mjs";

async function fixture(action) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "uar-workspace-"));
  try { await action(root, (operation, options = {}) => execute({ task_id: "task-one", branch: "main", operation, ...options }, { root, localTestRoot: root })); }
  finally { await fs.rm(root, { recursive: true, force: true }); }
}

test("production Agent refuses remote Git operations without executing commands", () => fixture(async (root) => {
  for (const operation of ["clone", "push"]) {
    let called = false;
    await assert.rejects(execute({ task_id: "task-one", operation, remote: "ssh://git@10.228.84.126:30022/test/test.git", publish_authorized: true },
      { root, run: async () => { called = true; return { success: true }; } }), /publication_rejected/);
    assert.equal(called, false);
  }
}));
function git(cwd, ...args) {
  const result = spawnSync("git", args, { cwd, encoding: "utf8", timeout: 30000, shell: false, env: { ...process.env, GIT_CONFIG_NOSYSTEM: "1", GIT_CONFIG_GLOBAL: process.platform === "win32" ? "NUL" : "/dev/null" } });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim();
}

test("native operations clone/add/commit/push into a bare remote, independently verified", () => fixture(async (root, op) => {
  const remote = path.join(root, "remote.git");
  git(root, "init", "--bare", "--initial-branch=main", remote);
  await op("prepare"); await op("clone", { remote });
  await op("write", { files: [{ path: "src/main/java/App.java", content: "public class App {}\n" }] });
  assert.match((await op("status")).output, /App.java/);
  await op("add", { paths: ["src/main/java/App.java"] });
  assert.match((await op("diff")).output, /public class App/);
  const commit = await op("commit");
  assert.match(commit.commit_id, /^[0-9a-f]{40}$/);
  await assert.rejects(op("push", { remote }), /publication_rejected/);
  await op("push", { remote, publish_authorized: true });
  assert.equal(git(root, "--git-dir", remote, "rev-parse", "refs/heads/main"), commit.commit_id);
  assert.equal(git(root, "--git-dir", remote, "show", "main:src/main/java/App.java"), "public class App {}");
}));

test("path traversal, reserved files, case collisions and shell operations are rejected", () => fixture(async (root, op) => {
  await op("prepare");
  for (const name of ["../outside", "/absolute", "C:/absolute", ".git/config", ".env", ".gitattributes", "a\\b", "a/./b", "NUL.txt", "a/../b"])
    assert.throws(() => projectPath(name), /workspace_rejected/);
  await assert.rejects(op("write", { files: [{ path: "A.java", content: "a" }, { path: "a.java", content: "b" }] }), /workspace_rejected/);
  await op("init");
  await assert.rejects(op("execute_shell_as_root", { command: "anything" }), /workspace_rejected/);
  assert.equal(await fs.readFile(path.join(root, "projects/task-one/.git/HEAD"), "utf8"), "ref: refs/heads/main\n");
}));

test("untrusted local Git config cannot execute filters, hooks or credential helpers", () => fixture(async (root, op) => {
  await op("prepare"); await op("init");
  const config = path.join(root, "projects/task-one/.git/config");
  const original = await fs.readFile(config, "utf8");
  for (const addition of ['\n[filter "evil"]\n clean = arbitrary\n', '\n[include]\n path = /etc/gitconfig\n', '\n[credential]\n helper = arbitrary\n', '\n[core]\n hooksPath = /tmp/hooks\n']) {
    await fs.writeFile(config, original + addition);
    await assert.rejects(op("status"), /workspace_rejected/);
  }
}));

test("hard-linked project files cannot overwrite an external file", () => fixture(async (root, op) => {
  await op("prepare");
  const outside = path.join(root, "outside.txt");
  await fs.writeFile(outside, "preserve");
  await fs.link(outside, path.join(root, "projects/task-one/linked.txt"));
  await assert.rejects(op("write", { files: [{ path: "linked.txt", content: "overwrite" }] }), /workspace_rejected/);
  assert.equal(await fs.readFile(outside, "utf8"), "preserve");
}));

test("execution timeout and output limits are enforced without shell", async () => {
  const failed = await runBounded([process.execPath, "-e", "process.exit(7)"], os.tmpdir(), {}, 3000);
  assert.equal(failed.success, false);
  assert.equal(failed.exit_code, 7);
  const passed = await runBounded([process.execPath, "-e", "process.stdout.write('ok')"], os.tmpdir(), {}, 3000);
  assert.equal(passed.success, true);
  assert.equal(passed.exit_code, 0);
  await assert.rejects(runBounded([process.execPath, "-e", "setTimeout(()=>{}, 10000)"], os.tmpdir(), {}, 30), /operation_timeout/);
  await assert.rejects(runBounded([process.execPath, "-e", "process.stdout.write('x'.repeat(100000))"], os.tmpdir(), {}, 3000), /output_limit/);
});

test("build commands use fixed argv, workspace and sanitized environment", () => fixture(async (root, op) => {
  await op("prepare"); await op("init");
  await op("write", { files: [{ path: "pom.xml", content: "<project/>" }] });
  const calls = [];
  const run = async (argv, cwd, env) => { calls.push({ argv, cwd, env }); return { success: false, output: "compile error" }; };
  const build = (operation, build_system) => execute({ task_id: "task-one", operation, build_system }, { root, run });
  assert.deepEqual(await build("test", "maven"), { success: false, output: "compile error", check: "mvn test" });
  await build("package", "maven");
  await build("test", "gradle");
  await op("write", { files: [{ path: "gradlew", content: "#!/bin/bash\n" }] });
  await fs.mkdir(path.join(root, "projects/task-one/gradle/wrapper"), { recursive: true });
  await fs.writeFile(path.join(root, "projects/task-one/gradle/wrapper/gradle-wrapper.jar"), Buffer.from([0x50, 0x4b, 0, 0]));
  await build("package", "gradle");
  assert.equal(calls[0].argv[0], "mvn");
  assert.equal(calls[1].argv.at(-1), "package");
  assert.deepEqual(calls[2].argv, ["gradle", "--no-daemon", "--console=plain", "test"]);
  assert.deepEqual(calls[3].argv, ["bash", "./gradlew", "--no-daemon", "--console=plain", "build"]);
  for (const call of calls) {
    assert.equal(call.cwd, path.join(root, "projects/task-one"));
    for (const key of ["OPENAI_API_KEY", "SFERA_TOKEN", "JAVA_TOOL_OPTIONS", "MAVEN_OPTS", "GRADLE_OPTS", "NODE_OPTIONS", "GIT_CONFIG_COUNT"])
      assert.equal(call.env[key], undefined);
  }
  await assert.rejects(build("test", "shell"), /workspace_rejected/);
}));
