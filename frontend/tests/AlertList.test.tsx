import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AlertList from "../src/components/AlertList";

const alert = (id: string, symbol: string) => ({
  id,
  rule_id: "r",
  symbol,
  rule_type: "price_above",
  threshold: 100,
  price: 101.5,
  triggered_at: 1700000000,
});

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });

describe("AlertList", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("loads once on mount and refresh shows new alerts", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(ok([alert("1", "AAPL")]))
      .mockResolvedValueOnce(ok([alert("2", "MSFT"), alert("1", "AAPL")]));
    vi.stubGlobal("fetch", fetchMock);
    render(<AlertList />);
    await waitFor(() => expect(screen.getByText(/AAPL/)).toBeTruthy());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Last updated/)).toBeTruthy();

    fireEvent.click(screen.getByText("Refresh"));
    await waitFor(() => expect(screen.getByText(/MSFT/)).toBeTruthy());
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("shows an error, not an empty message, then recovers", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, status: 503, json: async () => ({}) })
      .mockResolvedValueOnce(ok([]));
    vi.stubGlobal("fetch", fetchMock);
    render(<AlertList />);
    await waitFor(() => expect(screen.getByText(/Could not load alerts/)).toBeTruthy());
    expect(screen.queryByText(/No alerts/)).toBeNull();

    fireEvent.click(screen.getByText("Refresh"));
    await waitFor(() => expect(screen.getByText(/No alerts/)).toBeTruthy());
    expect(screen.queryByText(/Could not load alerts/)).toBeNull();
  });

  it("handles a rejected fetch", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("net")));
    render(<AlertList />);
    await waitFor(() => expect(screen.getByText(/Could not load alerts/)).toBeTruthy());
    expect(screen.queryByText(/No alerts/)).toBeNull();
  });
});
