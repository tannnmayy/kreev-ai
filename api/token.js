// A token for one new room, whose configuration dispatches Ala into it. The same job as ala/web/server.py, for Vercel.
import crypto from "node:crypto";
import { AccessToken, RoomAgentDispatch, RoomConfiguration } from "livekit-server-sdk";
import { settings } from "./_livekit.js";

const clip = (value, n) => String(value || "").split(/\s+/).filter(Boolean).join(" ").slice(0, n);

export default async function handler(req, res) {
  const s = settings();
  if (!s.url || !s.key || !s.secret) return res.status(503).send("LiveKit isn't configured on this deployment.");
  const q = req.query || {};
  if (s.invite && q.invite !== s.invite) return res.status(403).send("This demo needs an invite code. It's in the link you were sent.");
  const session = String(q.session || "").replace(/[^\w-]/g, "").slice(0, 40) || crypto.randomBytes(8).toString("hex");
  const room = "ala-" + crypto.randomBytes(4).toString("hex");
  const name = clip(q.name, 40);
  const metadata = JSON.stringify({ session, name, lang: q.lang || "" });
  const token = new AccessToken(s.key, s.secret, { identity: "customer-" + crypto.randomBytes(3).toString("hex"), name: name || "Customer", ttl: "30m" });
  token.addGrant({ roomJoin: true, room, canPublish: true, canSubscribe: true, canPublishData: true });
  token.roomConfig = new RoomConfiguration({ agents: [new RoomAgentDispatch({ agentName: s.agent, metadata })] });
  res.status(200).json({ url: s.url, token: await token.toJwt(), room, session });
}
