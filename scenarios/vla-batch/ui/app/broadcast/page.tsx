import ScreenCarousel from "@/components/broadcast/ScreenCarousel";
import { broadcastScreens } from "@/lib/broadcast/screens";

/**
 * De bron van de YouTube-livestream. Een renderer opent deze pagina in een
 * kiosk-Chromium op 1280×720 en neemt het scherm op met ffmpeg.
 * Bedoeld voor dat ene venster, niet om in te klikken; zie
 * scenarios/vla-batch/broadcast/README.md.
 *
 * /broadcast          2D-schermen; de renderer op de VPS (geen GPU).
 * /broadcast?twin=1   met de 3D-twin vooraan; de Pi 5 (wel een GPU).
 */
export const metadata = {
  title: "Broadcast, DairyWorks Vla",
  robots: { index: false, follow: false },
};

export default async function BroadcastPage({
  searchParams,
}: {
  searchParams: Promise<{ twin?: string }>;
}) {
  const { twin } = await searchParams;
  return <ScreenCarousel screens={broadcastScreens({ twin: twin === "1" })} />;
}
