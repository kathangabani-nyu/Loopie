import { ProductShell } from "@/components/loopie-product/shell";
import { ResourcePage } from "@/components/loopie-product/resource-page";

export default function ArtifactsPage() { return <ProductShell><ResourcePage title="Artifact Time Machine" description="Postgres is authoritative; Redis is a reconciled live projection." path="artifacts" columns={["artifact_key", "latest", "versions"]} /></ProductShell>; }
