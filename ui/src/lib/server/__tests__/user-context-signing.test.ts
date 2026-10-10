/** @jest-environment node */
import { createHmac } from "node:crypto";
import { buildSignedUserContextHeaders } from "../user-context-signing";

describe("gateway context signing", () => {
  const original = process.env.DA_USER_CONTEXT_HMAC_SECRET;
  beforeEach(() => { process.env.DA_USER_CONTEXT_HMAC_SECRET = "test-signing-key"; });
  afterEach(() => { if (original === undefined) delete process.env.DA_USER_CONTEXT_HMAC_SECRET; else process.env.DA_USER_CONTEXT_HMAC_SECRET = original; });
  it("binds the timestamp, bearer and context in the backend wire format", () => {
    const headers = buildSignedUserContextHeaders("encoded-context", "Bearer test-token");
    const digest = createHmac("sha256", "test-signing-key")
      .update(`${headers["X-User-Context-Timestamp"]}\nBearer test-token\nencoded-context`).digest("hex");
    expect(headers["X-User-Context-Signature"]).toBe(`v2=${digest}`);
    expect(buildSignedUserContextHeaders("encoded-context", "Bearer another-token")["X-User-Context-Signature"]).not.toBe(`v2=${digest}`);
  });
  it("fails closed without a secret or bearer", () => {
    expect(() => buildSignedUserContextHeaders("context", "")).toThrow("bearer token");
    delete process.env.DA_USER_CONTEXT_HMAC_SECRET;
    expect(() => buildSignedUserContextHeaders("context", "Bearer token")).toThrow("not configured");
  });
  it("preserves explicitly enabled local dev login without a bearer", () => {
    const originalEnv = { ...process.env };
    try {
      Object.assign(process.env, {
        NODE_ENV: "development", SSO_ENABLED: "false",
        ALLOW_DEV_ADMIN_WHEN_SSO_DISABLED: "true", CAIPE_UNSAFE_RBAC_BYPASS: "true",
      });
      delete process.env.DA_USER_CONTEXT_HMAC_SECRET;
      expect(buildSignedUserContextHeaders("dev-context", "Bearer ")).toEqual({
        "X-User-Context": "dev-context",
      });
      process.env.NODE_ENV = "production";
      expect(() => buildSignedUserContextHeaders("dev-context", "")).toThrow("not configured");
    } finally {
      process.env = originalEnv;
    }
  });
});
