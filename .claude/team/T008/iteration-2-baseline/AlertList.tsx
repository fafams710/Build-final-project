import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, listAlerts } from "../api/client";

export default function AlertList() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [updated, setUpdated] = useState<Date | null>(null);
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const data = await listAlerts();
      if (!mounted.current) return;
      setAlerts(data);
      setError(null);
      setUpdated(new Date());
    } catch {
      if (!mounted.current) return;
      setError("Could not load alerts.");
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    refresh();
    return () => {
      mounted.current = false;
    };
  }, [refresh]);

  return (
    <div>
      <h2>Alerts</h2>
      <button onClick={refresh}>Refresh</button>
      {updated && <span> Last updated: {updated.toLocaleTimeString()}</span>}
      {error && <p role="alert">{error}</p>}
      {!error && updated && alerts.length === 0 && <p>No alerts.</p>}
      {alerts.length > 0 && (
        <ul>
          {alerts.map((a) => (
            <li key={a.id}>
              {a.symbol} {a.rule_type} {a.threshold} - price {a.price} at{" "}
              {new Date(a.triggered_at * 1000).toLocaleString()}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
