import { FailureDetail } from "@/components/loopie-product/failure-detail";
import { ProductShell } from "@/components/loopie-product/shell";

export default async function FailurePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ProductShell><FailureDetail failureId={id} /></ProductShell>;
}
