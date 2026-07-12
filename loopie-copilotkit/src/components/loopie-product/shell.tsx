import Link from "next/link";

import "./product.css";

const links = [
  ["/", "Overview"],
  ["/tickets", "Tickets"],
  ["/runs", "Runs"],
  ["/corrections", "Corrections"],
  ["/triage", "Triage"],
  ["/policies", "Policies"],
  ["/artifacts", "Artifacts"],
  ["/assistant", "Assistant"],
] as const;

export function ProductShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="loopie-product">
      <div className="lp-shell">
        <aside className="lp-sidebar">
          <div className="lp-brand"><span className="lp-mark">L</span><span>Loopie</span></div>
          <nav className="lp-nav" aria-label="Product navigation">
            {links.map(([href, label]) => <Link key={href} href={href}>{label}</Link>)}
          </nav>
        </aside>
        <main className="lp-main">{children}</main>
      </div>
    </div>
  );
}
