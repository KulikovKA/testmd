#!/usr/bin/env node
// Read-only Sfera Task MCP bridge. Qwen cannot select a URL, method, header,
// request body, or operation beyond get_task.

import fs from "node:fs";
import http from "node:http";
import https from "node:https";
import { fileURLToPath } from "node:url";

const ENTITY_NUMBER = /^[A-Z][A-Z0-9]{1,31}-[1-9][0-9]{0,9}$/;
const MAX_RESPONSE_BYTES = Number.parseInt(process.env.UAR_SFERA_MAX_RESPONSE_BYTES || "65536", 10);
const TIMEOUT_MS = Number.parseInt(process.env.UAR_SFERA_TIMEOUT_MS || "10000", 10);
const baseUrl = parseBaseUrl(process.env.UAR_SFERA_BASE_URL || "");
const username = process.env.UAR_SFERA_USERNAME || "";
const password = process.env.UAR_SFERA_PASSWORD || "";
const customCaPath = process.env.NODE_EXTRA_CA_CERTS || "";
const allowed = new Set((process.env.UAR_AGENT_TOOL_CAPABILITIES || "").split(",").filter((name) => name === "get_task"));
let sessionCookie = null;

function parseBaseUrl(value) {
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || !url.hostname || url.username || url.password || url.search || url.hash) return null;
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

function validConfiguration() {
  return baseUrl && username && password && Number.isInteger(MAX_RESPONSE_BYTES) && MAX_RESPONSE_BYTES >= 1024 && Number.isInteger(TIMEOUT_MS) && TIMEOUT_MS >= 1;
}

function validEntityNumber(value) {
  return typeof value === "string" && ENTITY_NUMBER.test(value);
}

function cookies(response) {
  const raw = response.headers["set-cookie"];
  const values = Array.isArray(raw)
    ? raw
    : typeof raw === "string"
      ? raw.split(/,(?=[^;,]+=)/)
      : [];
  return values.filter((value) => typeof value === "string" && value.includes("=")).map((value) => value.split(";", 1)[0]).join("; ");
}

async function readResponse(response) {
  const length = response.headers["content-length"];
  const declared = Number.parseInt(Array.isArray(length) ? length[0] : length || "0", 10);
  if (declared > MAX_RESPONSE_BYTES) throw new Error("response_limit");
  const chunks = [];
  let received = 0;
  for await (const chunk of response) {
    const bytes = Buffer.from(chunk);
    received += bytes.length;
    if (received > MAX_RESPONSE_BYTES) throw new Error("response_limit");
    chunks.push(bytes);
  }
  return Buffer.concat(chunks).toString("utf8");
}

export function sferaRequestOptions(url, options) {
  const headers = { ...options.headers };
  if (options.body && !Object.hasOwn(headers, "content-length")) {
    headers["content-length"] = Buffer.byteLength(options.body);
  }
  const result = {
    hostname: url.hostname,
    port: url.port || undefined,
    path: `${url.pathname}${url.search}`,
    method: options.method,
    headers,
  };
  if (url.protocol === "https:" && customCaPath) {
    result.ca = fs.readFileSync(customCaPath);
    result.allowPartialTrustChain = true;
  }
  return result;
}

async function fetchWithTimeout(url, options) {
  let request;
  const response = new Promise((resolveResponse, rejectResponse) => {
    const transport = url.protocol === "https:" ? https : http;
    request = transport.request(sferaRequestOptions(url, options), resolveResponse);
    request.once("error", rejectResponse);
    if (options.body) request.write(options.body);
    request.end();
  });
  const timeout = new Error("timeout");
  timeout.name = "AbortError";
  const timer = setTimeout(() => request?.destroy(timeout), TIMEOUT_MS);
  try {
    const incoming = await response;
    return { response: incoming, raw: await readResponse(incoming) };
  }
  finally { clearTimeout(timer); }
}

async function login() {
  const { response } = await fetchWithTimeout(new URL("/app/ppau/api/auth/login", baseUrl), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (response.statusCode === 401 || response.statusCode === 403) throw new Error("authentication_failed");
  if (response.statusCode < 200 || response.statusCode >= 300) throw new Error("service_failure");
  const value = cookies(response);
  if (!value) throw new Error("authentication_failed");
  sessionCookie = value;
}

function safeText(value, minimum, maximum) {
  return typeof value === "string" && value.length >= minimum && value.length <= maximum && !value.includes("\0");
}

function named(value) {
  if (!object(value) || !safeText(value.identifier, 1, 128) || !safeText(value.name, 1, 512)) throw new Error("invalid_schema");
  return { identifier: value.identifier, name: value.name };
}

function normalizedPositiveId(value) {
  if (typeof value === "number") {
    return Number.isSafeInteger(value) && value > 0 ? value : null;
  }
  if (typeof value !== "string" || !/^[0-9]+$/.test(value) || !/[1-9]/.test(value)) return null;
  const numeric = Number(value);
  return Number.isSafeInteger(numeric) ? numeric : value;
}

function child(value) {
  const numericId = object(value) ? normalizedPositiveId(value.id) : null;
  if (!object(value) || numericId === null || !validEntityNumber(value.number) || !safeText(value.name, 1, 2000)) throw new Error("invalid_schema");
  return { numeric_id: numericId, number: value.number, title: value.name };
}

function normalizeTask(value) {
  const numericId = object(value) ? normalizedPositiveId(value.id) : null;
  if (!object(value) || numericId === null || !validEntityNumber(value.number) || !safeText(value.name, 1, 2000) || !safeText(value.description, 0, 16000) || !Array.isArray(value.children) || value.children.length > 100) throw new Error("invalid_schema");
  const normalized = {
    number: value.number,
    numeric_id: numericId,
    title: value.name,
    description: value.description,
    state: safeText(value.state, 1, 128) ? value.state : null,
    status: named(value.status),
    type: named(value.type),
    priority: named(value.priority),
    area: named(value.area),
    children: value.children.map(child),
  };
  if (Buffer.byteLength(JSON.stringify(normalized), "utf8") > MAX_RESPONSE_BYTES) throw new Error("response_limit");
  return normalized;
}

async function getTask(entityNumber) {
  if (!sessionCookie) await login();
  const url = new URL(`/app/tasks/api/v1/entity-views/${encodeURIComponent(entityNumber)}`, baseUrl);
  let { response, raw } = await fetchWithTimeout(url, { method: "GET", headers: { cookie: sessionCookie } });
  if (response.statusCode === 401) {
    sessionCookie = null;
    await login();
    ({ response, raw } = await fetchWithTimeout(url, { method: "GET", headers: { cookie: sessionCookie } }));
  }
  if (response.statusCode === 404) throw new Error("not_found");
  if (response.statusCode === 401 || response.statusCode === 403) throw new Error("authentication_failed");
  if (response.statusCode === 408 || response.statusCode === 504) throw new Error("timeout");
  if (response.statusCode < 200 || response.statusCode >= 300) throw new Error("service_failure");
  let payload;
  try { payload = JSON.parse(raw); } catch { throw new Error("invalid_schema"); }
  return normalizeTask(payload);
}

async function call(name, input) {
  if (name !== "get_task" || !allowed.has(name)) return diagnostic("capability_denied", "operation is not enabled for this Agent");
  if (!object(input) || Object.keys(input).length !== 1 || !validEntityNumber(input.entity_number)) return diagnostic("invalid_input", "entity_number is invalid");
  if (!validConfiguration()) return diagnostic("service_failure", "Sfera deployment configuration is invalid");
  try { return textResult(await getTask(input.entity_number)); }
  catch (error) {
    const code = error?.name === "AbortError" ? "timeout" : error?.message;
    const messages = {
      authentication_failed: "Sfera rejected credentials",
      not_found: "Task was not found",
      timeout: "Sfera request timed out",
      response_limit: "Sfera response exceeded the configured limit",
      invalid_schema: "Sfera returned an invalid response",
    };
    return diagnostic(Object.hasOwn(messages, code) ? code : "service_failure", messages[code] || "Sfera service is unavailable");
  }
}

const definition = {
  description: "Read one Sfera Task by its entity number. This operation never changes Task data.",
  inputSchema: { type: "object", additionalProperties: false, required: ["entity_number"], properties: { entity_number: { type: "string", pattern: "^[A-Z][A-Z0-9]{1,31}-[1-9][0-9]{0,9}$" } } },
};

function reply(id, result) { process.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id, result })}\n`); }
function error(id, code, message) { process.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } })}\n`); }

function startStdioServer() {
  let buffer = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => {
    buffer += chunk;
    let end;
    while ((end = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, end);
      buffer = buffer.slice(end + 1);
      if (!line.trim()) continue;
      Promise.resolve().then(async () => {
        let request;
        try { request = JSON.parse(line); } catch { return; }
        if (!object(request) || request.jsonrpc !== "2.0" || typeof request.method !== "string") return;
        const id = request.id;
        if (request.method === "initialize") return reply(id, { protocolVersion: request.params?.protocolVersion || "2024-11-05", capabilities: { tools: {} }, serverInfo: { name: "restricted-sfera-task", version: "1.0.0" } });
        if (request.method === "tools/list") return reply(id, { tools: allowed.has("get_task") ? [{ name: "get_task", ...definition }] : [] });
        if (request.method === "tools/call") return reply(id, await call(request.params?.name, request.params?.arguments));
        if (id !== undefined) error(id, -32601, "method not found");
      });
    }
  });
}

function isEntryPoint() {
  try {
    return process.argv[1]
      && fs.realpathSync(process.argv[1]) === fs.realpathSync(fileURLToPath(import.meta.url));
  }
  catch { return false; }
}

if (isEntryPoint()) startStdioServer();
