// Wake a sleeping agent while the customer reads the page: a job that ends at once. Free plans scale agents to zero.
import crypto from "node:crypto";
import { AgentDispatchClient } from "livekit-server-sdk";
import { settings } from "./_livekit.js";

export default async function handler(req, res) {
  const s = settings();
  if (req.method !== "POST" || !s.url) return res.status(200).json({ ok: false });
  try {
    const client = new AgentDispatchClient(s.url.replace(/^ws/, "http"), s.key, s.secret);
    await client.createDispatch("warm-" + crypto.randomBytes(3).toString("hex"), s.agent, { metadata: JSON.stringify({ warm: true }) });
    res.status(200).json({ ok: true });
  } catch (error) {
    res.status(200).json({ ok: false, why: error.name });
  }
}
