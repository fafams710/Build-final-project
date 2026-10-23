import { FormEvent, useState } from "react";

export default function SymbolSubscribe({ onAdd }: { onAdd: (symbol: string) => void }) {
  const [value, setValue] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    onAdd(value);
    setValue("");
  };

  return (
    <form onSubmit={submit}>
      <input
        aria-label="symbol"
        placeholder="e.g. AAPL"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <button type="submit">Watch</button>
    </form>
  );
}
