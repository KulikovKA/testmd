#!/usr/bin/env node
// Narrow MCP bridge for the deployment-configured Task REST service.
// It deliberately accepts no URL, method, header, or arbitrary body from Qwen.

import fs from "node:fs";

const OPERATIONS = Object.freeze({
  get_task: { method: "GET", path: (input) => `/tasks/${encodeURIComponent(input.task_id)}` },
  create_task: { method: "POST", path: () => "/tasks" },
  create_subtask: { method: "POST", path: (input) => `/tasks/${encodeURIComponent(input.task_id)}/subtasks` },
  update_task: { method: "PATCH", path: (input) => `/tasks/${encodeURIComponent(input.task_id)}` },
});
const MUTATIONS = new Set(["create_task", "create_subtask", "update_task"]);
const TASK_ID = /^task-[0-9]{4,}$/;
const STATUSES = new Set(["open", "in_progress", "done", "cancelled"]);
const MAX_RESPONSE_BYTES = Number.parseInt(process.env.UAR_TASK_API_MAX_RESPONSE_BYTES || "65536", 10);
const TIMEOUT_MS = Number.parseInt(process.env.UAR_TASK_API_TIMEOUT_MS || "10000", 10);
const allowed = new Set(
  (process.env.UAR_AGENT_TOOL_CAPABILITIES || "").split(",").filter((name) => Object.hasOwn(OPERATIONS, name)),
);
const baseUrl = parseBaseUrl(process.env.UAR_TASK_API_BASE_URL || "");
const token = process.env.UAR_TASK_API_TOKEN || "";
const resultLog = process.env.UAR_TASK_RESULT_LOG || "";
const completedCalls = new Map();

function parseBaseUrl(value) {
  try {
    const url = new URL(value);
    if (
      !["http:", "https:"].includes(url.protocol) || !url.hostname ||
      url.username || url.password || url.search || url.hash
    ) return null;
    return url;
  } catch { return null; }
}

function textResult(value, isError = false) {
  return { content: [{ type: "text", text: JSON.stringify(value) }], ...(isError ? { isError: true } : {}) };
}

function diagnostic(code, message) {
  return textResult({ error: { code, message } }, true);
}

function object(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function exactly(value, keys) {
  return object(value) && Object.keys(value).every((key) => keys.includes(key));
}

function safeText(value, minimum, maximum) {
  return typeof value === "string" && value.length >= minimum && value.length <= maximum && !value.includes("\0");
}

function validTaskId(value) { return typeof value === "string" && TASK_ID.test(value); }

function validate(operation, input) {
  if (!object(input)) return "input must be an object";
  if (operation === "get_task") {
    return exactly(input, ["task_id"]) && validTaskId(input.task_id) ? null : "task_id is invalid";
  }
  if (operation === "create_task") {
    return exactly(input, ["title", "description"]) && safeText(input.title, 1, 200) &&
      (input.description === undefined || safeText(input.description, 0, 4000)) ? null : "title or description is invalid";
  }
  if (operation === "create_subtask") {
    return exactly(input, ["task_id", "title", "description"]) && validTaskId(input.task_id) &&
      safeText(input.title, 1, 200) && (input.description === undefined || safeText(input.description, 0, 4000)) ? null : "subtask input is invalid";
  }
  if (operation === "update_task") {
    if (!exactly(input, ["task_id", "expected_version", "title", "description", "status"])) return "update input contains unsupported fields";
    if (!validTaskId(input.task_id) || !Number.isInteger(input.expected_version) || input.expected_version < 1) return "task_id or expected_version is invalid";
    const change = input.title !== undefined || input.description !== undefined || input.status !== undefined;
    return change && (input.title === undefined || safeText(input.title, 1, 200)) &&
      (input.description === undefined || safeText(input.description, 0, 4000)) &&
      (input.status === undefined || STATUSES.has(input.status)) ? null : "update fields are invalid";
  }
  return "unsupported operation";
}

function requestBody(operation, input) {
  if (operation === "get_task") return undefined;
  if (operation === "create_task" || operation === "create_subtask") {
    return JSON.stringify({ title: input.title, description: input.description || "" });
  }
  const body = { expected_version: input.expected_version };
  for (const key of ["title", "description", "status"]) if (input[key] !== undefined) body[key] = input[key];
  return JSON.stringify(body);
}

function validTask(value) {
  return object(value) && validTaskId(value.id) && safeText(value.title, 1, 200) &&
    safeText(value.description, 0, 4000) && STATUSES.has(value.status) &&
    (value.parent_id === null || validTaskId(value.parent_id)) && Array.isArray(value.subtask_ids) &&
    value.subtask_ids.every(validTaskId) && Number.isInteger(value.version) && value.version >= 1 &&
    exactly(value, ["id", "title", "description", "status", "parent_id", "subtask_ids", "version"]);
}

function recordMutation(operation, task) {
  if (!resultLog || !MUTATIONS.has(operation)) return;
  try {
    fs.appendFileSync(resultLog, `${JSON.stringify({ operation, task })}\n`, { encoding: "utf8" });
  } catch {
    // The validated Task result is still returned to Qwen. The caller treats a
    // missing result journal as absent evidence and never fabricates a record.
  }
}

async function call(operation, input) {
  if (!allowed.has(operation)) return diagnostic("capability_denied", "operation is not enabled for this Agent");
  const invalid = validate(operation, input);
  if (invalid) return diagnostic("invalid_input", invalid);
  if (!baseUrl || !Number.isInteger(MAX_RESPONSE_BYTES) || MAX_RESPONSE_BYTES < 1024 || !Number.isInteger(TIMEOUT_MS) || TIMEOUT_MS < 1) {
    return diagnostic("service_failure", "Task service deployment configuration is invalid");
  }
  const url = new URL(OPERATIONS[operation].path(input), baseUrl);
  const body = requestBody(operation, input);
  const callKey = `${OPERATIONS[operation].method} ${url.pathname}\n${body || ""}`;
  const completed = MUTATIONS.has(operation) ? completedCalls.get(callKey) : undefined;
  if (completed !== undefined) return completed;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const headers = body === undefined ? {} : { "content-type": "application/json" };
    if (token) headers.authorization = `Bearer ${token}`;
    const response = await fetch(url, { method: OPERATIONS[operation].method, headers, body, signal: controller.signal });
    const declared = Number.parseInt(response.headers.get("content-length") || "0", 10);
    if (declared > MAX_RESPONSE_BYTES) return diagnostic("service_failure", "Task service response exceeded the configured limit");
    const chunks = [];
    let received = 0;
    if (response.body) {
      for await (const chunk of response.body) {
        const bytes = Buffer.from(chunk);
        received += bytes.length;
        if (received > MAX_RESPONSE_BYTES) return diagnostic("service_failure", "Task service response exceeded the configured limit");
        chunks.push(bytes);
      }
    }
    const raw = Buffer.concat(chunks).toString("utf8");
    let payload;
    try { payload = JSON.parse(raw); } catch { return diagnostic("service_failure", "Task service returned an invalid response"); }
    if (response.status === 404) return diagnostic("not_found", "task was not found");
    if (response.status === 401 || response.status === 403) return diagnostic("authentication_failed", "Task service rejected credentials");
    if (response.status === 408 || response.status === 504) return diagnostic("timeout", "Task service timed out");
    if (response.status === 422 || response.status === 409 || response.status === 400) return diagnostic("invalid_input", "Task service rejected the fixed request schema");
    if (!response.ok || !validTask(payload)) return diagnostic("service_failure", "Task service failed or returned an invalid schema");
    recordMutation(operation, payload);
    const result = textResult(payload);
    if (MUTATIONS.has(operation)) completedCalls.set(callKey, result);
    return result;
  } catch (error) {
    return diagnostic(error?.name === "AbortError" ? "timeout" : "service_failure", error?.name === "AbortError" ? "Task service timed out" : "Task service is unavailable");
  } finally { clearTimeout(timer); }
}

const definitions = {
  get_task: { description: "Get one Task by its fixed task identifier.", inputSchema: { type: "object", additionalProperties: false, required: ["task_id"], properties: { task_id: { type: "string", pattern: "^task-[0-9]{4,}$" } } } },
  create_task: { description: "Create one Task with a title and optional description.", inputSchema: { type: "object", additionalProperties: false, required: ["title"], properties: { title: { type: "string", minLength: 1, maxLength: 200 }, description: { type: "string", maxLength: 4000 } } } },
  create_subtask: { description: "Create one child Task beneath an existing Task.", inputSchema: { type: "object", additionalProperties: false, required: ["task_id", "title"], properties: { task_id: { type: "string", pattern: "^task-[0-9]{4,}$" }, title: { type: "string", minLength: 1, maxLength: 200 }, description: { type: "string", maxLength: 4000 } } } },
  update_task: { description: "Update selected fields of one Task using its expected version.", inputSchema: { type: "object", additionalProperties: false, required: ["task_id", "expected_version"], properties: { task_id: { type: "string", pattern: "^task-[0-9]{4,}$" }, expected_version: { type: "integer", minimum: 1 }, title: { type: "string", minLength: 1, maxLength: 200 }, description: { type: "string", maxLength: 4000 }, status: { type: "string", enum: [...STATUSES] } } } },
};

function reply(id, result) { process.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id, result })}\n`); }
function error(id, code, message) { process.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } })}\n`); }

let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let end;
  while ((end = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, end); buffer = buffer.slice(end + 1);
    if (!line.trim()) continue;
    Promise.resolve().then(async () => {
      let request;
      try { request = JSON.parse(line); } catch { return; }
      if (!object(request) || request.jsonrpc !== "2.0" || typeof request.method !== "string") return;
      const id = request.id;
      if (request.method === "initialize") return reply(id, { protocolVersion: request.params?.protocolVersion || "2024-11-05", capabilities: { tools: {} }, serverInfo: { name: "restricted-task-rest", version: "1.0.0" } });
      if (request.method === "tools/list") return reply(id, { tools: Object.entries(definitions).filter(([name]) => allowed.has(name)).map(([name, definition]) => ({ name, ...definition })) });
      if (request.method === "tools/call") {
        const name = request.params?.name;
        if (!Object.hasOwn(OPERATIONS, name)) return reply(id, diagnostic("capability_denied", "unknown Task operation"));
        return reply(id, await call(name, request.params?.arguments));
      }
      if (id !== undefined) error(id, -32601, "method not found");
    });
  }
});
