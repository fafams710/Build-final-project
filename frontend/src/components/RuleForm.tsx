import { FormEvent, useState } from "react";
import { createRule, RuleType } from "../api/client";

export default function RuleForm({ onCreated }: { onCreated: () => void }) {
  const [symbol, setSymbol] = useState("");
  const [type, setType] = useState<RuleType>("price_above");
  const [threshold, setThreshold] = useState("");
  const [windowSeconds, setWindowSeconds] = useState("60");
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await createRule({
        symbol: symbol.trim().toUpperCase(),
        type,
        threshold: Number(threshold),
        window_seconds: type === "percent_change" ? Number(windowSeconds) : undefined,
      });
      setThreshold("");
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create rule");
    }
  };

  return (
    <form onSubmit={submit}>
      <input aria-label="rule symbol" placeholder="Symbol" value={symbol} onChange={(e) => setSymbol(e.target.value)} required />
      <select aria-label="rule type" value={type} onChange={(e) => setType(e.target.value as RuleType)}>
        <option value="price_above">Price above</option>
        <option value="price_below">Price below</option>
        <option value="percent_change">Percent change over window</option>
      </select>
      <input aria-label="threshold" type="number" step="any" min="0" placeholder={type === "percent_change" ? "Percent" : "Price"} value={threshold} onChange={(e) => setThreshold(e.target.value)} required />
      {type === "percent_change" && (
        <input aria-label="window seconds" type="number" min="1" value={windowSeconds} onChange={(e) => setWindowSeconds(e.target.value)} required />
      )}
      <button type="submit">Create rule</button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
