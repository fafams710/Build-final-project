import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Dashboard from "../src/components/Dashboard";
import { wsUrl } from "../src/api/client";

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string) {
    MockWebSocket.instances.push(this);
  }
  send() {}
  close() {
    this.readyState = 3;
  }
}

const okFetch = () =>
  vi.fn(async (_url: string, init?: RequestInit) => ({
    ok: true,
    status: init?.method === "POST" ? 201 : 200,
    json: async () => (init?.method === "POST" ? { id: "1" } : []),
  }));

describe("API key", () => {
  beforeEach(() => {
    sessionStorage.clear();
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
  });
  afterEach(() => {
    cleanup();
    sessionStorage.clear();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("shows only the key prompt without a stored key", () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<Dashboard />);
    const input = screen.getByLabelText("API key") as HTMLInputElement;
    expect(input.type).toBe("password");
    expect(screen.getByText("Save")).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(MockWebSocket.instances.length).toBe(0);
    expect(screen.queryByText("Alert rules")).toBeNull();
  });

  it("Save stores the key and later calls carry X-API-Key", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<Dashboard />);
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "k-1" } });
    fireEvent.click(screen.getByText("Save"));
    expect(sessionStorage.getItem("apiKey")).toBe("k-1");
    await waitFor(() => expect(screen.getByText("Alert rules")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("rule symbol"), { target: { value: "msft" } });
    fireEvent.change(screen.getByLabelText("threshold"), { target: { value: "300" } });
    fireEvent.click(screen.getByText("Create rule"));
    await waitFor(() => expect(fetchMock.mock.calls.some((c) => c[1]?.method === "POST")).toBe(true));

    expect(fetchMock.mock.calls.length).toBeGreaterThan(2);
    for (const [, init] of fetchMock.mock.calls) {
      expect((init!.headers as Record<string, string>)["X-API-Key"]).toBe("k-1");
    }
    const post = fetchMock.mock.calls.find((c) => c[1]?.method === "POST")!;
    expect((post[1]!.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("blank Save does nothing", () => {
    vi.stubGlobal("fetch", okFetch());
    render(<Dashboard />);
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "   " } });
    fireEvent.click(screen.getByText("Save"));
    expect(sessionStorage.getItem("apiKey")).toBeNull();
    expect(screen.queryByText("Alert rules")).toBeNull();
  });

  it("websocket url carries the encoded key", () => {
    sessionStorage.setItem("apiKey", "a+b/c=&");
    vi.stubGlobal("fetch", okFetch());
    render(<Dashboard />);
    expect(MockWebSocket.instances[0].url.endsWith("/ws?key=" + encodeURIComponent("a+b/c=&"))).toBe(true);
  });

  it("appends with & when the configured ws url has a query", () => {
    sessionStorage.setItem("apiKey", "k");
    vi.stubEnv("VITE_WS_URL", "ws://h/ws?x=1");
    expect(wsUrl()).toBe("ws://h/ws?x=1&key=k");
  });

  it("a 401 clears the key, closes the socket and shows the prompt", async () => {
    sessionStorage.setItem("apiKey", "bad");
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 401, json: async () => ({}) })));
    render(<Dashboard />);
    await waitFor(() => expect(screen.getByLabelText("API key")).toBeTruthy());
    expect(sessionStorage.getItem("apiKey")).toBeNull();
    expect(MockWebSocket.instances[0].readyState).toBe(3);
  });

  it("a 500 does not clear the key", async () => {
    sessionStorage.setItem("apiKey", "good");
    const fetchMock = vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) }));
    vi.stubGlobal("fetch", fetchMock);
    render(<Dashboard />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await act(async () => {});
    expect(sessionStorage.getItem("apiKey")).toBe("good");
    expect(screen.getByText("Alert rules")).toBeTruthy();
  });
});
