export type RuleType = "price_above" | "price_below" | "percent_change";

export interface RuleInput {
  symbol: string;
  type: RuleType;
  threshold: number;
  window_seconds?: number;
}

export interface Rule extends RuleInput {
  id: string;
}

const base = (import.meta.env?.VITE_API_URL as string | undefined) ?? "";

const KEY_NAME = "apiKey";
const unauthorizedListeners = new Set<() => void>();

export function getApiKey(): string | null {
  return sessionStorage.getItem(KEY_NAME);
}

export function setApiKey(key: string): void {
  sessionStorage.setItem(KEY_NAME, key);
}

export function clearApiKey(): void {
  sessionStorage.removeItem(KEY_NAME);
}

/** Called after a 401 has cleared the stored key. Returns an unsubscribe fn. */
export function onUnauthorized(fn: () => void): () => void {
  unauthorizedListeners.add(fn);
  return () => {
    unauthorizedListeners.delete(fn);
  };
}

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return { ...extra, "X-API-Key": getApiKey() ?? "" };
}

async function check(res: Response): Promise<Response> {
  if (res.status === 401) {
    clearApiKey();
    unauthorizedListeners.forEach((fn) => fn());
  }
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res;
}

export async function listRules(): Promise<Rule[]> {
  const res = await check(await fetch(`${base}/api/rules`, { headers: authHeaders() }));
  return res.json();
}

export async function createRule(rule: RuleInput): Promise<Rule> {
  const res = await check(
    await fetch(`${base}/api/rules`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(rule),
    })
  );
  return res.json();
}

export async function deleteRule(id: string): Promise<void> {
  await check(await fetch(`${base}/api/rules/${id}`, { method: "DELETE", headers: authHeaders() }));
}

export function wsUrl(): string {
  const configured = import.meta.env?.VITE_WS_URL as string | undefined;
  let url: string;
  if (configured) {
    url = configured;
  } else {
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    url = `${scheme}://${window.location.host}/ws`;
  }
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}key=${encodeURIComponent(getApiKey() ?? "")}`;
}

export interface Alert {
  id: string;
  rule_id: string;
  symbol: string;
  rule_type: string;
  threshold: number;
  price: number;
  triggered_at: number;
}

export async function listAlerts(limit = 50): Promise<Alert[]> {
  const res = await check(await fetch(`${base}/api/alerts?limit=${limit}`, { headers: authHeaders() }));
  return res.json();
}
