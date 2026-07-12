import { ProductShell } from "@/components/loopie-product/shell";
import { ResourcePage } from "@/components/loopie-product/resource-page";

export default function RunsPage() { return <ProductShell><ResourcePage title="Runs" description="Durable execution state with pinned manifests and explicit failure status." path="runs?limit=200" columns={["id", "status", "mode", "kind", "ticket_id", "manifest_id", "error", "created_at"]} /></ProductShell>; }
