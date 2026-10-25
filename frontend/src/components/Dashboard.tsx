import { useCallback, useEffect, useState } from "react";
import { wsUrl } from "../api/client";
import { useWebSocket } from "../hooks/useWebSocket";
import AlertList from "./AlertList";
import PriceTicker from "./PriceTicker";
import RuleForm from "./RuleForm";
import RuleList from "./RuleList";
import SymbolSubscribe from "./SymbolSubscribe";

export default function Dashboard() {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [prices, setPrices] = useState<Record<string, number>>({});
  const [rulesVersion, setRulesVersion] = useState(0);

  const onMessage = useCallback((msg: any) => {
    if ((msg.type === "tick" || msg.type === "snapshot") && typeof msg.price === "number") {
      setPrices((p) => ({ ...p, [msg.symbol]: msg.price }));
    }
  }, []);

  const { status, send } = useWebSocket(wsUrl(), onMessage);

  // (Re)subscribe everything watched whenever the socket (re)opens.
  useEffect(() => {
    if (status === "open") {
      symbols.forEach((symbol) => send({ action: "subscribe", symbol }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  const add = (raw: string) => {
    const symbol = raw.trim().toUpperCase();
    if (!symbol || symbols.includes(symbol)) return;
    setSymbols((s) => [...s, symbol]);
    send({ action: "subscribe", symbol });
  };

  const remove = (symbol: string) => {
    setSymbols((s) => s.filter((x) => x !== symbol));
    setPrices((p) => {
      const { [symbol]: _gone, ...rest } = p;
      return rest;
    });
    send({ action: "unsubscribe", symbol });
  };

  return (
    <div>
      <p>Connection: {status}</p>
      <SymbolSubscribe onAdd={add} />
      <PriceTicker symbols={symbols} prices={prices} onRemove={remove} />
      <h2>Alert rules</h2>
      <RuleForm onCreated={() => setRulesVersion((v) => v + 1)} />
      <RuleList version={rulesVersion} />
      <AlertList />
    </div>
  );
}
