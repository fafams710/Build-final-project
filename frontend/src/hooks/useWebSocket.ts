import { useCallback, useEffect, useRef, useState } from "react";

export type WsStatus = "connecting" | "open" | "closed";

/** Opens a WebSocket, reconnects with capped backoff, exposes send + status. */
export function useWebSocket(url: string, onMessage: (data: any) => void) {
  const [status, setStatus] = useState<WsStatus>("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;

  useEffect(() => {
    let stopped = false;
    let retry = 1000;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      setStatus("connecting");
      const ws = new WebSocket(url);
      wsRef.current = ws;
      ws.onopen = () => {
        retry = 1000;
        setStatus("open");
      };
      ws.onmessage = (ev) => {
        try {
          handlerRef.current(JSON.parse(ev.data));
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        setStatus("closed");
        if (!stopped) {
          timer = setTimeout(connect, retry);
          retry = Math.min(retry * 2, 15000);
        }
      };
    };
    connect();

    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      wsRef.current?.close();
    };
  }, [url]);

  const send = useCallback((payload: unknown) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify(payload));
    }
  }, []);

  return { status, send };
}
