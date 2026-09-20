import assert from "node:assert/strict";
import test from "node:test";
import { capabilities } from "../agent_image/capabilities.mjs";

function toolResult(name) {
  return { status: 0, stdout: name === "javac" ? "javac 21.0.8" : "", stderr: name === "java" ? 'openjdk version "21.0.8"' : "" };
}

test("capabilities require Java 21, compiler and every production tool without shell execution", () => {
  const called = [];
  const result = capabilities((name, args, options) => {
    called.push(name);
    assert.equal(options.shell, false);
    assert.ok(options.timeout > 0 && options.maxBuffer > 0);
    assert.ok(args.every((argument) => argument.startsWith("-")));
    return toolResult(name);
  });
  assert.equal(result.ready, true);
  assert.deepEqual(called.sort(), ["bash", "curl", "git", "gradle", "java", "javac", "mvn", "node", "qwen", "unzip"]);
});

test("Java 17, missing compiler, unavailable tool and timeout fail without diagnostics leakage", () => {
  for (const unavailable of ["java", "javac", "mvn", "gradle"]) {
    const result = capabilities((name) => name !== unavailable ? toolResult(name)
      : { status: 1, error: new Error("private diagnostic"), stdout: 'openjdk version "17.0.18"', stderr: "private diagnostic" });
    assert.equal(result.ready, false);
    assert.equal(result.tools[unavailable].available, false);
    assert.ok(!JSON.stringify(result).includes("private diagnostic"));
  }
  const result = capabilities((name) => name === "java"
    ? { status: 0, stderr: 'openjdk version "17.0.18"' } : toolResult(name));
  assert.equal(result.tools.java.major, 17);
  assert.equal(result.ready, false);
});
