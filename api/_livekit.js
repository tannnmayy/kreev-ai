// Shared settings for the serverless functions. The secret stays here: browsers only get a short-lived token for one room.
export const settings = () => ({
  url: process.env.LIVEKIT_URL || "",
  key: process.env.LIVEKIT_API_KEY || "",
  secret: process.env.LIVEKIT_API_SECRET || "",
  agent: process.env.ALA_AGENT_NAME || "ala",
  invite: process.env.ALA_INVITE_CODE || "",
});
