import { Suspense } from "react";
import { Preparation } from "@/components/screens/Preparation";

export const metadata = { title: "Voorbereiding, DairyWorks" };

export default function VoorbereidingPage() {
  return (
    <Suspense>
      <Preparation />
    </Suspense>
  );
}
