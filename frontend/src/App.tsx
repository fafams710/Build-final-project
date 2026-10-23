import Dashboard from "./components/Dashboard";

export default function App() {
  return (
    <main style={{ fontFamily: "sans-serif", maxWidth: 720, margin: "2rem auto" }}>
      <h1>Price Tracker</h1>
      <Dashboard />
    </main>
  );
}
