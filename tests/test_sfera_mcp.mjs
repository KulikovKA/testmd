import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { once } from "node:events";
import test from "node:test";

const serverPath = process.env.MCP_SERVER || new URL("../src/universal_agent_runtime/adapters/task_rest_mcp_server.mjs", import.meta.url).pathname;
const secret = "sfera-password-must-not-leak";

const task = {
  id: 225,
  number: "TTEST2-94",
  name: "Проверка Sfera",
  description: "Только для теста.",
  state: "Normal",
  status: { identifier: "created", name: "Создано" },
  type: { identifier: "task", name: "Задача" },
  priority: { identifier: "average", name: "Средний" },
  area: { identifier: "TTEST2", name: "Декомпозитор Эпиков" },
  children: [],
};

class McpClient {
  constructor(endpoint, { maxResponseBytes = "65536" } = {}) {
    this.child = spawn(process.execPath, [serverPath], {
      env: {
        ...process.env,
        UAR_SFERA_BASE_URL: endpoint,
        UAR_SFERA_USERNAME: "sfera-user",
        UAR_SFERA_PASSWORD: secret,
        UAR_SFERA_TIMEOUT_MS: "1000",
        UAR_SFERA_MAX_RESPONSE_BYTES: maxResponseBytes,
        UAR_AGENT_TOOL_CAPABILITIES: "get_task,create_task",
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    this.nextId = 1;
    this.buffer = "";
    this.pending = new Map();
    this.child.stdout.setEncoding("utf8");
    this.child.stdout.on("data", (chunk) => {
      this.buffer += chunk;
      let end;
      while ((end = this.buffer.indexOf("\n")) >= 0) {
        const line = this.buffer.slice(0, end);
        this.buffer = this.buffer.slice(end + 1);
        const response = JSON.parse(line);
        const resolve = this.pending.get(response.id);
        if (resolve) {
          this.pending.delete(response.id);
          resolve(response);
        }
      }
    });
  }

  request(method, params) {
    const id = this.nextId++;
    this.child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
    return new Promise((resolve) => this.pending.set(id, resolve));
  }

  async close() {
    this.child.kill();
    await once(this.child, "exit");
  }
}

async function withMcp(handler, action, options = {}) {
  const calls = [];
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks).toString("utf8");
    calls.push({ method: request.method, path: request.url, headers: request.headers, body });
    await handler(request, response, calls);
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  const client = new McpClient(`http://127.0.0.1:${address.port}`, options);
  try { await action(client, calls); }
  finally {
    await client.close();
    server.close();
    await once(server, "close");
  }
}

function result(response) {
  return JSON.parse(response.result.content[0].text);
}

function login(response, value = "one") {
  response.writeHead(200, { "Set-Cookie": [`SESSION=${value}; HttpOnly`, `ROUTE=${value}; Path=/`] });
  response.end("{}");
}

test("get_task logs in with exact JSON, retains all cookies, and exposes only get_task", async () => {
  await withMcp(async (request, response) => {
    if (request.url === "/app/ppau/api/auth/login") return login(response);
    assert.equal(request.url, "/app/tasks/api/v1/entity-views/TTEST2-94");
    assert.match(request.headers.cookie, /SESSION=one/);
    assert.match(request.headers.cookie, /ROUTE=one/);
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify(task));
  }, async (mcp, calls) => {
    const listed = await mcp.request("tools/list", {});
    assert.deepEqual(listed.result.tools.map((tool) => tool.name), ["get_task"]);
    const first = result(await mcp.request("tools/call", { name: "get_task", arguments: { entity_number: "TTEST2-94" } }));
    const second = result(await mcp.request("tools/call", { name: "get_task", arguments: { entity_number: "TTEST2-94" } }));
    assert.equal(first.numeric_id, 225);
    assert.equal(first.title, task.name);
    assert.deepEqual(second.children, []);
    assert.equal(calls.filter((call) => call.path === "/app/ppau/api/auth/login").length, 1);
    assert.equal(calls[0].method, "POST");
    assert.equal(calls[0].headers["content-type"], "application/json");
    assert.equal(calls[0].body, JSON.stringify({ username: "sfera-user", password: secret }));
  });
});

test("one entity 401 invalidates cookies, logs in once more, and retries once", async () => {
  let reads = 0;
  await withMcp(async (request, response, calls) => {
    if (request.url === "/app/ppau/api/auth/login") return login(response, String(calls.length));
    reads += 1;
    if (reads === 1) return response.writeHead(401).end("{}");
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify(task));
  }, async (mcp, calls) => {
    const response = await mcp.request("tools/call", { name: "get_task", arguments: { entity_number: "TTEST2-94" } });
    assert.equal(response.result.isError, undefined);
    assert.equal(result(response).number, "TTEST2-94");
    assert.equal(calls.filter((call) => call.path === "/app/ppau/api/auth/login").length, 2);
    assert.equal(reads, 2);
  });
});

test("invalid input never reaches Sfera and authentication failures do not expose credentials", async () => {
  await withMcp(async (_request, response) => response.writeHead(401).end("{}"), async (mcp, calls) => {
    const invalid = await mcp.request("tools/call", { name: "get_task", arguments: { entity_number: "TTEST2-94?method=DELETE" } });
    assert.equal(result(invalid).error.code, "invalid_input");
    assert.equal(calls.length, 0);
    const denied = await mcp.request("tools/call", { name: "create_task", arguments: {} });
    assert.equal(result(denied).error.code, "capability_denied");
    const failed = await mcp.request("tools/call", { name: "get_task", arguments: { entity_number: "TTEST2-94" } });
    assert.equal(result(failed).error.code, "authentication_failed");
    assert.doesNotMatch(JSON.stringify(failed), new RegExp(secret));
  });
});

for (const [name, status, body, code] of [
  ["not found", 404, "{}", "not_found"],
  ["forbidden", 403, "{}", "authentication_failed"],
  ["invalid JSON", 200, "not-json", "invalid_schema"],
  ["invalid schema", 200, JSON.stringify({ ...task, children: "not-an-array" }), "invalid_schema"],
]) {
  test(`get_task handles ${name} safely`, async () => {
    await withMcp(async (request, response) => {
      if (request.url === "/app/ppau/api/auth/login") return login(response);
      response.writeHead(status, { "content-type": "application/json" });
      response.end(body);
    }, async (mcp) => {
      const response = await mcp.request("tools/call", { name: "get_task", arguments: { entity_number: "TTEST2-94" } });
      assert.equal(result(response).error.code, code);
      assert.doesNotMatch(JSON.stringify(response), new RegExp(secret));
    });
  });
}

test("get_task enforces the configured response-size limit", async () => {
  await withMcp(async (request, response) => {
    if (request.url === "/app/ppau/api/auth/login") return login(response);
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ ...task, description: "x".repeat(2000) }));
  }, async (mcp) => {
    const response = await mcp.request("tools/call", { name: "get_task", arguments: { entity_number: "TTEST2-94" } });
    assert.equal(result(response).error.code, "response_limit");
  }, { maxResponseBytes: "1024" });
});
