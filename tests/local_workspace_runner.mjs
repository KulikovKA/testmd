// Test-only entry point: local transport is never enabled by the production CLI.
import { execute } from "../agent_image/workspace-operations.mjs";
try {
  const result = await execute(JSON.parse(process.argv[3]), { root: process.argv[2], localTestRoot: process.argv[2] });
  process.stdout.write(JSON.stringify(result));
} catch (error) {
  process.stdout.write(JSON.stringify({ success: false, error: error.code || "operation_failed" }));
}
