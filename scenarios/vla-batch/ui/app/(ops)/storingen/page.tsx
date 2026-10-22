import { Suspense } from "react";
import { FaultPanel } from "@/components/screens/FaultPanel";

export const metadata = { title: "Storingen, DairyWorks Vla-B" };

export default function StoringenPage() {
  return (
    <Suspense>
      <FaultPanel />
    </Suspense>
  );
}
