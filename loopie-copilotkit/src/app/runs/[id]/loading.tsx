import { ProductShell } from "@/components/loopie-product/shell";

export default function LoadingRun() {
  return (
    <ProductShell>
      <header className="lp-header">
        <div>
          <h1>Run evidence</h1>
          <p>Loading the pinned manifest and execution evidence…</p>
        </div>
        <span className="lp-pill" data-status="running">Loading</span>
      </header>
      <div className="lp-empty">Loading run details…</div>
    </ProductShell>
  );
}
