import { ProductShell } from "@/components/loopie-product/shell";
import { RunDetail } from "@/components/loopie-product/run-detail";

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ProductShell><RunDetail runId={id} /></ProductShell>;
}
