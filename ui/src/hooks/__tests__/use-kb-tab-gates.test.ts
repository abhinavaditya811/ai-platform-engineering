import { renderHook, waitFor } from "@testing-library/react";
import { useSession } from "next-auth/react";
import { useKbTabGates } from "../use-kb-tab-gates";

jest.mock("next-auth/react", () => ({
  useSession: jest.fn(),
}));

describe("useKbTabGates", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn();
    (useSession as jest.Mock).mockReturnValue({ data: null, status: "unauthenticated" });
  });

  it("fails closed when unauthenticated", async () => {
    const { result } = renderHook(() => useKbTabGates());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.orgAdminBypass).toBe(false);
    expect(result.current.gates.has_any_kb).toBe(false);
    expect(global.fetch).not.toHaveBeenCalled();
  });
});
