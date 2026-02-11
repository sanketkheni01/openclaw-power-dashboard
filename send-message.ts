import { callGateway } from "/root/openclaw/src/gateway/call.js";
import { randomUUID } from "node:crypto";

const [sessionKey, message] = [process.argv[2], process.argv[3]];
if (!sessionKey || !message) {
  process.stdout.write(JSON.stringify({ error: "sessionKey and message required" }));
  process.exit(1);
}

try {
  const result = await callGateway({
    method: "chat.send",
    params: { sessionKey, message, idempotencyKey: randomUUID() },
    expectFinal: true,
    timeoutMs: 120000,
  });
  process.stdout.write(JSON.stringify({ status: "sent" }));
} catch (e: any) {
  process.stdout.write(JSON.stringify({ error: e.message }));
  process.exit(1);
}
