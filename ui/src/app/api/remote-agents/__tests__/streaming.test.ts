const mockCollection = {
  findOne: jest.fn(),
  insertOne: jest.fn(),
  findOneAndUpdate: jest.fn(),
};
jest.mock("@/lib/mongodb", () => ({ getCollection: async () => mockCollection }));
jest.mock("@/lib/api-middleware", () => ({
  ApiError: class ApiError extends Error {},
  getAuthFromBearerOrSession: async () => ({ user: { email: "test-user@example.test" }, session: {} }),
  requireRbacPermission: async () => undefined,
  successResponse: (data: unknown, status = 200) => ({ status, json: async () => ({ success: true, data }) }),
  withErrorHandler: (handler: unknown) => handler,
}));
jest.mock("@/lib/remote-agent-auth", () => ({
  normalizeRemoteAgentCredentialSource: () => ({ kind: "caller_token", target: "header", name: "Authorization" }),
}));

import { POST } from "../route";
import { PUT } from "../[id]/route";

const request = (body: unknown) => ({ json: async () => body }) as never;

beforeEach(() => {
  jest.clearAllMocks();
  mockCollection.findOne.mockResolvedValue(null);
  mockCollection.insertOne.mockResolvedValue({});
  mockCollection.findOneAndUpdate.mockResolvedValue({ _id: "remote-example", streaming: true });
});

it.each([true, false, undefined])("persists opt-in streaming %s with an off default", async streaming => {
  await POST(request({ name: "Example", endpoint: "https://agent.example.test/", streaming }));
  expect(mockCollection.insertOne).toHaveBeenCalledWith(expect.objectContaining({ streaming: streaming === true }));
});

it.each(["true", 1, null])("rejects a non-boolean streaming flag %s", async streaming => {
  await expect(POST(request({ name: "Example", endpoint: "https://agent.example.test/", streaming }))).rejects.toThrow("Streaming must be a boolean");
  await expect(PUT(request({ streaming }), { params: Promise.resolve({ id: "remote-example" }) })).rejects.toThrow("Streaming must be a boolean");
});

it("updates streaming without changing other settings", async () => {
  await PUT(request({ streaming: true }), { params: Promise.resolve({ id: "remote-example" }) });
  expect(mockCollection.findOneAndUpdate).toHaveBeenCalledWith(
    expect.objectContaining({ _id: "remote-example" }),
    { $set: expect.objectContaining({ streaming: true }) },
    { returnDocument: "after" },
  );
});
