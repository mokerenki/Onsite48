"use client";

import { useParams } from "next/navigation";
import OperatorTab from "@/components/operatorTab";

export default function OperatorPage() {
  const params = useParams<{ threadId: string }>();
  const threadId = params && typeof params.threadId === "string" ? params.threadId : "";
  return threadId ? <OperatorTab threadId={threadId} /> : <div className="p-6 text-sm">A task identifier is required.</div>;
}