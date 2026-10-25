import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Dashboard from "../src/components/Dashboard";

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string) {
    MockWebSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  receive(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
}

describe("Dashboard", () => {
  beforeEach(() => {
    sessionStorage.setItem("apiKey", "test-key");
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, status: 200, json: async () => [] }))
    );
  });
  afterEach(() => {
    cleanup();
    sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  it("subscribes to a symbol and shows live prices", async () => {
    render(<Dashboard />);
    const ws = MockWebSocket.instances[0];
    act(() => ws.open());

    fireEvent.change(screen.getByLabelText("symbol"), { target: { value: "aapl" } });
    fireEvent.click(screen.getByText("Watch"));
    expect(ws.sent.map((s) => JSON.parse(s))).toContainEqual({ action: "subscribe", symbol: "AAPL" });

    act(() => ws.receive({ type: "tick", symbol: "AAPL", price: 123.456, timestamp: 1 }));
    await waitFor(() => expect(screen.getByTestId("price-AAPL").textContent).toBe("123.46"));

    fireEvent.click(screen.getByLabelText("remove AAPL"));
    expect(ws.sent.map((s) => JSON.parse(s))).toContainEqual({ action: "unsubscribe", symbol: "AAPL" });
    expect(screen.queryByTestId("row-AAPL")).toBeNull();
  });

  it("creates a rule through the rules API", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => ({
      ok: true,
      status: init?.method === "POST" ? 201 : 200,
      json: async () => (init?.method === "POST" ? { id: "1" } : []),
    }));
    vi.stubGlobal("fetch", fetchMock);
    render(<Dashboard />);

    fireEvent.change(screen.getByLabelText("rule symbol"), { target: { value: "msft" } });
    fireEvent.change(screen.getByLabelText("threshold"), { target: { value: "300" } });
    fireEvent.click(screen.getByText("Create rule"));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find((c) => c[1]?.method === "POST");
      expect(post).toBeTruthy();
      expect(JSON.parse(post![1]!.body as string)).toMatchObject({
        symbol: "MSFT",
        type: "price_above",
        threshold: 300,
      });
      expect(Object.keys(JSON.parse(post![1]!.body as string)).sort()).toEqual(["symbol", "threshold", "type"]);
    });
  });
});
