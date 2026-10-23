interface Props {
  symbols: string[];
  prices: Record<string, number>;
  onRemove: (symbol: string) => void;
}

export default function PriceTicker({ symbols, prices, onRemove }: Props) {
  if (symbols.length === 0) return <p>No symbols watched.</p>;
  return (
    <ul>
      {symbols.map((s) => (
        <li key={s} data-testid={`row-${s}`}>
          <strong>{s}</strong>{" "}
          <span data-testid={`price-${s}`}>{prices[s] !== undefined ? prices[s].toFixed(2) : "--"}</span>{" "}
          <button onClick={() => onRemove(s)} aria-label={`remove ${s}`}>
            Remove
          </button>
        </li>
      ))}
    </ul>
  );
}
