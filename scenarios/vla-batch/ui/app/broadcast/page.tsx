import ScreenCarousel from "@/components/broadcast/ScreenCarousel";
import { BROADCAST_SCREENS } from "@/lib/broadcast/screens";

/**
 * De bron van de YouTube-livestream. De renderer-container opent deze pagina
 * in een kiosk-Chromium op 1280×720 en neemt het scherm op met ffmpeg.
 * Bedoeld voor dat ene venster, niet om in te klikken; zie
 * scenarios/vla-batch/broadcast/README.md.
 */
export const metadata = {
  title: "Broadcast, DairyWorks Vla",
  robots: { index: false, follow: false },
};

export default function BroadcastPage() {
  return <ScreenCarousel screens={BROADCAST_SCREENS} />;
}
