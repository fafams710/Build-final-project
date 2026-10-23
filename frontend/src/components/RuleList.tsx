import { useCallback, useEffect, useState } from "react";
import { deleteRule, listRules, Rule } from "../api/client";

export default function RuleList({ version }: { version: number }) {
  const [rules, setRules] = useState<Rule[]>([]);

  const refresh = useCallback(async () => {
    try {
      setRules(await listRules());
    } catch {
      setRules([]);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, version]);

  const remove = async (id: string) => {
    try {
      await deleteRule(id);
    } finally {
      refresh();
    }
  };

  if (rules.length === 0) return <p>No rules.</p>;
  return (
    <ul>
      {rules.map((r) => (
        <li key={r.id}>
          {r.symbol} {r.type} {r.threshold}
          {r.window_seconds ? ` / ${r.window_seconds}s` : ""}{" "}
          <button onClick={() => remove(r.id)}>Delete</button>
        </li>
      ))}
    </ul>
  );
}
