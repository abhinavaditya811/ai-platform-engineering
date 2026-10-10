import { createHmac } from "node:crypto";
import { ApiError } from "@/lib/api-error";

/** Bind gateway authorization flags to this bearer and a short validity window. */
export function buildSignedUserContextHeaders(
  encodedUserContext: string,
  authorization: string,
): Record<string, string> {
  const secret = process.env.DA_USER_CONTEXT_HMAC_SECRET?.trim();
  if (!secret) throw new ApiError("DA_USER_CONTEXT_HMAC_SECRET is not configured", 503);
  const bearer = authorization.trim();
  if (!/^Bearer \S+$/i.test(bearer)) throw new ApiError("A bearer token is required for Dynamic Agents", 401, "BEARER_REQUIRED", "session_expired", "sign_in");
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const signature = createHmac("sha256", secret)
    .update(`${timestamp}\n${bearer}\n${encodedUserContext}`)
    .digest("hex");
  return {
    "X-User-Context": encodedUserContext,
    "X-User-Context-Timestamp": timestamp,
    "X-User-Context-Signature": `v2=${signature}`,
  };
}
