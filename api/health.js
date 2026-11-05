// What an uptime monitor checks. Reports which settings exist, never their values.
import { RoomServiceClient } from "livekit-server-sdk";
import { settings } from "./_livekit.js";

export default async function handler(_req, res) {
  const s = settings();
  let livekit = false;
  try {
    if (s.url) {
      await new RoomServiceClient(s.url.replace(/^ws/, "http"), s.key, s.secret).listRooms();
      livekit = true;
    }
  } catch { livekit = false; }
  res.status(livekit ? 200 : 503).json({ ok: livekit, livekit, invite_required: Boolean(s.invite) });
}
