export type RuleType = "price_above" | "price_below" | "percent_change";

export interface RuleInput {
  symbol: string;
  type: RuleType;
  threshold: number;
  window_seconds?: number;
  webhook_url: string;
}

export interface Rule extends RuleInput {
  id: string;
}

const base = (import.meta.env?.VITE_API_URL as string | undefined) ?? "";

async function check(res: Response): Promise<Response> {
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res;
}

export async function listRules(): Promise<Rule[]> {
  const res = await check(await fetch(`${base}/api/rules`));
  return res.json();
}

export async function createRule(rule: RuleInput): Promise<Rule> {
  const res = await check(
    await fetch(`${base}/api/rules`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(rule),
    })
  );
  return res.json();
}

export async function deleteRule(id: string): Promise<void> {
  await check(await fetch(`${base}/api/rules/${id}`, { method: "DELETE" }));
}

export function wsUrl(): string {
  const configured = import.meta.env?.VITE_WS_URL as string | undefined;
  if (configured) return configured;
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/ws`;
}
