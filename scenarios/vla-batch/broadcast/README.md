# DairyWorks-livestream (`vla-broadcast`)

De milkdemo als YouTube-livestream: de 3D-site-twin en de productieschermen in een carrousel,
opgenomen op de VPS en verstuurd naar YouTube. **Unlisted**, niet-commercieel, alleen voor wie de
link krijgt. Dat volgt de DairyWorks-regels: eerst unlisted, en niet makkelijk vindbaar voor
ICT-collega's.

Dit document vervangt het oorspronkelijke voorstel "Dairy Digital Twin Livestream". Dat ging uit
van een andere situatie dan er werkelijk draait.

## Wat het voorstel aannam, en wat er werkelijk is

Meting en code van 20-11-2026.

| Voorstel | Werkelijkheid | Gevolg |
|---|---|---|
| VPS met 2 vCPU en 4 GB | 2 vCPU, **7,8 GB**, ongeveer 3,6 GB beschikbaar | Geheugen is niet de krapte. **De CPU is het wel**: 70 tot 77% idle, maar de batch-engine werkt in stoten tot 85%, en Hostinger-steal liep op 18-11 op tot ongeveer 70% |
| Twee losse HTTPS-domeinen voor Grafana en de twin | Eén origin. De site staat op `milkdemo.techflow24.com/`, Grafana op `/grafana` (`grafana.techflow24.com` is een 302). Beide zitten achter Caddy basic auth, en Grafana heeft daarnaast een eigen login. Anonymous staat bewust uit | Een iframe naar de publieke URL loopt vast op basic auth, `X-Frame-Options: SAMEORIGIN` en de Grafana-login |
| 3D-techniek onbekend | Three.js via React Three Fiber, en zwaar: schaduwen met een 2048-shadowmap, `Environment`, `dpr [1,2]`, antialias, `frameloop "always"` en meer dan vijftien `useFrame`-lussen | Zonder GPU tekent Chromium dit op de CPU (SwiftShader). Daarom is er een licht uitzendprofiel |
| Een nieuwe Next.js-app in `/opt/dairy-broadcast` | `vla-ui` is al Next.js 15 met standalone-output | `/broadcast` is een route in vla-ui. Dat is same-origin: geen frame-headers, geen CSP en geen tweede login |
| Een eigen netwerk `dairy-network` | `idp-network` bestaat al, vla-ui en Grafana hangen eraan | De renderer hangt aan `idp-network` en opent `http://vla-ui:3000/broadcast` rechtstreeks, zonder Caddy en zonder basic auth |
| Een SSH-tunnel naar poort 3000 | vla-ui staat al op `127.0.0.1:8091` | `ssh -L 18091:127.0.0.1:8091 techflow-vps`, daarna `http://localhost:18091/broadcast` |
| Bash-lus die ffmpeg en Chromium herstart | | Die lus verbergt fouten. Valt een proces om, dan stopt de container en herstart Docker hem |

## Architectuur

```
idp-network (bestaand)
 ├─ vla-ui:3000 ── /broadcast ── carrousel van same-origin iframes, elk 45 s:
 │                               /?embed=1            plant overview
 │                               /line?embed=1        lijnen L1 en L2
 │                               /management?embed=1  KPI's en verliezen
 │                               /equipment?embed=1   CIP, draaiuren, OEE
 │                  (de 3D-twin /site?embed=1&broadcast=1 is er na de meetproef uit gehaald)
 └─ vla-broadcast-renderer  (eigen compose-project -p vla-broadcast)
      Xvfb 1280x720 → Chromium kiosk → ffmpeg x11grab
      → libx264 ultrafast 2500k, 15 fps, GOP 2 s, stil AAC-spoor
      → MODE=test: /dev/null      MODE=youtube: rtmps://a.rtmps.youtube.com/live2/<key>
```

**Waarom 2D en 15 fps.** In de eerste meetproef haalde de 3D-twin zonder GPU maar 0,3 à 0,4 fps,
en raakte de host verzadigd (zie onder). De 2D-schermen veranderen alleen bij een data-update, elke
1,5 à 5 s. Daarvoor is 15 fps ruim, en 2500k is genoeg voor 720p met weinig beweging.

**In vla-ui:**

- `ui/app/broadcast/page.tsx`
- `ui/components/broadcast/ScreenCarousel.tsx`: één iframe tegelijk. Het volgende scherm laadt
  onzichtbaar en neemt pas over 2,5 s na zijn load-event, zodat de kijker nooit "3D-scène laden"
  ziet. Daarna verdwijnt het oude iframe.
- `ui/lib/broadcast/screens.ts`: de schermen en hun duur.
- `?embed=1`: de navigatie verdwijnt (`components/nav/Nav.tsx`).
- `?broadcast=1` op `/site`: geen schaduwen, geen `Environment`, `dpr 1`, geen antialias, een vaste
  20 fps via `frameloop="demand"` plus `invalidate()`, geen blur op de glaskaarten, geen
  bedieningsknoppen, en presentatiemodus met ronddraaiende camera. `/site` zonder parameter
  gedraagt zich als voorheen.

**De renderer** (`broadcast/`):

- `docker-compose.broadcast.yml`: `cpus: "1.0"`, `mem_limit: 1536m`, `shm_size: 512mb`, geen
  poorten. De demo gaat voor: haalt de renderer zijn beeld niet binnen één kern, dan zakt de
  stream, niet de fabriek.
- `renderer/start.sh`. `MAX_RUNTIME` is standaard 12 uur; daarna volgt een geplande herstart
  tegen geheugenlekken, en de stream valt dan ongeveer een halve minuut weg.
- `renderer/fonts.conf`: zonder dit bestand wordt `sans-serif` in de container Liberation Mono.
- `renderer/chromium-policy.json`: zonder dit bestand verschijnt de vertaalbalk "Dutch / English"
  in beeld.

## Bediening (op de VPS)

```bash
cd /root/idp-os
scenarios/vla-batch/broadcast/broadcast.sh test 30   # meetproef, 30 min naar /dev/null
scenarios/vla-batch/broadcast/broadcast.sh status    # CPU, geheugen, ffmpeg speed=
scenarios/vla-batch/broadcast/broadcast.sh frame     # één PNG: rendert WebGL echt?
scenarios/vla-batch/broadcast/broadcast.sh start     # naar YouTube
scenarios/vla-batch/broadcast/broadcast.sh stop
```

Gebruik altijd `broadcast.sh` of `-p vla-broadcast`. Een kale `docker compose down` in
`/root/idp-os` raakt de demo.

**De pre-flight weigert te starten** bij minder dan 2 GB beschikbaar geheugen, bij minder dan 50%
CPU-idle (gemeten over 30 s), als vla-ui niet healthy is, of als `idp-network` ontbreekt. Met
`FORCE=1` sla je alleen de CPU-controle over. Load1 is bewust geen criterium: die schommelt hier
tussen 1,3 en 2,5 terwijl de machine 70 tot 77% idle is.

**De stream key zet je zelf.** Hij komt nooit in git en nooit in een chat:

```bash
install -m 600 /dev/null /root/idp-os/.broadcast.env
nano /root/idp-os/.broadcast.env        # YOUTUBE_STREAM_KEY=...
```

In YouTube Studio: Live → Stream, met de zichtbaarheid op **Verborgen (unlisted)**. Zet
"automatisch beëindigen" uit, anders stopt YouTube de uitzending bij elke herstart van de
renderer.

## Go of no-go

Na `broadcast.sh test 30` is het **go** als alles hieronder klopt:

- ffmpeg `speed` blijft rond 1,0x (minimaal 0,98x);
- de host is gemiddeld minstens 20% idle;
- er is geen OOM-kill;
- vla-ui blijft healthy;
- `/api/ui/model` antwoordt binnen 1 s;
- `frame` toont de twin.

Meetbronnen zijn `docker stats`, `vmstat` en het Grafana-dashboard `vps-health` (inclusief steal).

**Bij no-go**, in deze volgorde. Stap 1 en 3 zijn na meetproef 1 al gezet.

1. `BROADCAST_FPS=15`.
2. `BROADCAST_WIDTH=960 BROADCAST_HEIGHT=540`.
3. De 3D-scène uit `screens.ts` halen. De 2D-schermen zijn SVG en uPlot, en goedkoop.
4. Een aparte machine voor alleen de renderer, met de publieke URL en basic auth (zie stap 2
   hieronder voor de login).

Opschalen van deze VPS lost hoge steal niet op; die komt van de host.

**De 3D-twin terug in de stream** vraagt een GPU. Er zijn twee kandidaten, en geen van beide is
gemeten:

- **Een Raspberry Pi 5 (8 GB) thuis.**
  - Hij heeft een echte GPU (VideoCore VII, OpenGL ES 3.1 via Mesa). Schatting: 15 à 30 fps voor
    de twin met `broadcast=1`.
  - Hij heeft geen hardware-encoder voor H.264; de Pi 4 had die nog wel. x264 draait dus op de A76-kernen;
    720p kost naar schatting 1 à 1,5 kern.
  - Xvfb gaat niet, want Xvfb heeft geen GPU. Chromium moet in een echte sessie draaien, met een
    HDMI-dummy of headless KMS, en ffmpeg neemt dat scherm op met `x11grab` of `kmsgrab`.
  - Actieve koeling is nodig, en een stabiele upload van 3 à 4 Mbit/s.
  - Eerst een bankproef van een kwartier: fps tellen op `/site?broadcast=1`, met ffmpeg ernaast.
    Script en werkwijze: [Bankproef Raspberry Pi 5](#bankproef-raspberry-pi-5).
- **Een GPU-server.** Dat werkt, maar is duur voor een unlisted demo-stream.

Daarna volgen 1 uur naar YouTube en dan 24 uur met dezelfde meetpunten. Pas na een groene
24-uurstest mag de stream 24/7 blijven staan.

## Bankproef Raspberry Pi 5

[`pi5/bankproef.sh`](pi5/bankproef.sh) beantwoordt één vraag: rendert een Pi 5 de twin vloeiend
en encodeert hij tegelijk realtime? Hij stuurt niets naar YouTube en wijzigt niets op de VPS.

**Wat hij meet.** De encoder is dezelfde als in de VPS-renderer: libx264 ultrafast, 1280x720,
15 fps, 2500k. Daarnaast hasht hij elk opgenomen frame en telt hoe vaak een frame echt van het
vorige verschilt. Dat is dezelfde les als bij meetproef 1: `speed=1x` zegt alleen dat ffmpeg
bijhoudt, niet dat Chromium tekent. Elke 5 s legt hij CPU-idle, geheugen, temperatuur en
throttling vast. Vooraf controleert hij dat Chromium WebGL op de GPU doet en niet op
SwiftShader; zonder GPU stopt hij, want dan meet je wat op de VPS al no-go was.

| Meetpunt | Drempel |
|---|---|
| Chromium op de GPU | WebGL-renderer is V3D, geen SwiftShader of llvmpipe |
| ffmpeg `speed` | ≥ 0,98x, laagste waarde na de eerste 30 s |
| Echte beeldsnelheid (alleen voor de twin) | ≥ 13,5 fps: gemiddeld, in de slechtste minuut én in de laatste 2 minuten (opwarming) |
| CPU-idle | gemiddeld ≥ 20% |
| Temperatuur SoC | < 80 °C (de Pi 5 schakelt bij 85 °C terug) |
| Throttling en onderspanning | geen (`vcgencmd get_throttled`, ook "eerder opgetreden") |
| Chromium en ffmpeg | allebei heel gebleven |

**Opzet op de Pi.**

1. Raspberry Pi OS Bookworm 64-bit met desktop, actieve koeling, en een HDMI-monitor of
   HDMI-dummy. Zonder scherm draait er geen GPU-sessie.
2. De desktop op X11, want onder Wayland neemt `x11grab` een zwart scherm op:
   `sudo raspi-config nonint do_wayland W1 && sudo reboot`.
3. `sudo apt install chromium ffmpeg x11-xserver-utils x11-utils procps curl`
   (op oudere images heet het pakket `chromium-browser`).
4. De demo bereikbaar maken. Standaard opent het script `http://localhost:8091/site?broadcast=1`,
   dus via een tunnel naar vla-ui. Geef de Pi daarvoor een **eigen sleutel die alleen die ene
   poort mag doorsturen**, niet de sleutel van de laptop:

   ```bash
   # op de Pi
   ssh-keygen -t ed25519 -f ~/.ssh/pi-bankproef -N '' -C pi-bankproef
   cat ~/.ssh/pi-bankproef.pub
   ```

   Zet die regel op de VPS in `/root/.ssh/authorized_keys`, met deze beperking ervoor:

   ```text
   restrict,port-forwarding,permitopen="127.0.0.1:8091",command="/bin/false" ssh-ed25519 AAAA... pi-bankproef
   ```

   Dan op de Pi: `ssh -N -i ~/.ssh/pi-bankproef -L 8091:127.0.0.1:8091 root@147.93.56.23`.
   **Haal de regel na de proef weer weg.** Inloggegevens in de URL (`https://gebruiker:wachtwoord@…`)
   werken ook, maar komen dan in de shellgeschiedenis en in `ps`; liever niet.

**Draaien**, vanuit de desktopsessie of over ssh:

```bash
./bankproef.sh                                    # 15 min op de twin
DUUR=5 ./bankproef.sh                             # eerst kort, om de opzet te proberen
URL='http://localhost:8091/broadcast' ./bankproef.sh   # de 2D-carousel; beeldsnelheid dan alleen informatief
DISPLAY=:0 XAUTHORITY=$HOME/.Xauthority ./bankproef.sh # over ssh
```

Het script zet het scherm tijdelijk op 1280x720 en de screensaver uit, en zet beide na afloop
terug. De uitvoer staat in `~/bankproef-<datum>-<tijd>/`: `rapport.txt` met het oordeel,
`frame.png` (kijk of daar de twin staat en geen inlogscherm), de ruwe metingen en de opname van
ongeveer 275 MB. Exitcode 0 is go, 1 is no-go, 2 is een fout in de opzet.

**Wat hij niet meet:** de upload. Voor dit profiel vraagt YouTube een stabiele 3 à 4 Mbit/s.
Bij go is de volgende stap dan ook een uur naar YouTube unlisted vanaf de Pi, met dezelfde
meetpunten, en pas daarna 24 uur.

De telling zelf is lokaal nagerekend met een testbron van 8 echte beelden per seconde, opgenomen
op 15 fps: het script meet 8,0 fps, ook per minuut en over de laatste 2 minuten. Op een Pi is
hij nog niet gedraaid.

## Meetproef 1, 20-11-2026: no-go voor de 3D-twin op deze VPS

Opzet: `broadcast.sh test`, 720p25, renderer begrensd op 1 kern. De proef is na 21 minuten
afgebroken toen de host verzadigd raakte.

| Meetpunt | Uitkomst | Drempel | |
|---|---|---|---|
| ffmpeg `speed` | stabiel 1x, 25 fps, 21 min | ≥ 0,98x | go |
| OOM-kills en herstarts | 0 en 0, geheugen ongeveer 1,0 GB van 1,5 GB | geen | go |
| vla-ui en `/api/ui/model` | healthy, maximaal 12 ms | < 1 s | go |
| `frame` | de twin rendert (SwiftShader werkt) | twin zichtbaar | go |
| **Echte beeldsnelheid van de twin** | **3 à 4 verschillende frames per 10 s (0,3 à 0,4 fps)** | vloeiend | **no-go** |
| Host-idle | gemiddeld 23%, en in 62 van de 130 metingen onder 20% (dieptepunt 1%, steal tot 16%) | gemiddeld ≥ 20% | **no-go** |

Verdeling binnen de renderer-kern: Chromium (renderer- plus GPU-proces) ongeveer 70%, ffmpeg
ongeveer 18%, Xvfb 1%. De batch-engine gebruikte tegelijk gemiddeld 52%.

**Meet de beeldsnelheid zelf, niet de encoder.** `speed=1x` zegt alleen dat ffmpeg 25 frames per
seconde wegschrijft. Tekent Chromium er minder, dan herhaalt x11grab gewoon het vorige frame.
Tellen kan met:

```bash
docker exec vla-broadcast-renderer ffmpeg -f x11grab -framerate 25 -video_size 1280x720 -t 10 -i :99.0 \
  -vf "mpdecimate=hi=1:lo=1:frac=0" -f null - 2>&1 | grep -o "frame= *[0-9]*" | tail -1
```

Gebruik die strenge instelling. De standaard-`mpdecimate` gooit ook bijna-gelijke frames weg en
telt een langzaam draaiende camera daardoor te laag.

### Meetproef 2, 20-11-2026: go voor de 2D-stream

Opzet: 2D-carrousel (vier schermen), 720p15 en 2500k, de volle 30 minuten.

| Meetpunt | Uitkomst | Drempel | |
|---|---|---|---|
| Renderer | CPU gemiddeld 18% (maximaal 56%), geheugen 405 MB, 0 herstarts en 0 OOM | | go |
| Host-idle | gemiddeld 40%; 18 van de 175 metingen onder 20% (dieptepunt 1%) | gemiddeld ≥ 20% | go |
| `/api/ui/model` | 29 keer 200, maximaal 18 ms | < 1 s | go |
| Beeld | Inter-lettertype, geen vertaalbalk, geen schuifbalk, geen navigatie | | go |
| ffmpeg `speed` | 1x zolang zichtbaar | ≥ 0,98x | go, maar alleen deels gezien |

Die 18 metingen onder 20% vallen samen met stoten van de batch-engine en een rebuild van vla-ui
door een andere sessie, rond 13:30 UTC.

De `speed` was na 2 minuten niet meer te volgen. Oorzaak: ffmpeg schrijft `-stats` met een carriage
return, en Docker logt pas een regel bij een newline. Sindsdien schrijft ffmpeg zijn voortgang
naar `/tmp/ffmpeg-progress.txt`, en `broadcast.sh status` leest dat bestand. Bevestig daarom bij
het eerste YouTube-uur expliciet dat `speed` rond 1x blijft.

Uit de eerste proef kwamen ook drie fouten in de container. Alle drie zijn gerepareerd:

- `sans-serif` werd Liberation Mono;
- de vertaalbalk van Chromium stond in beeld;
- ffmpeg gaf een stroom `non monotonically increasing dts`-waarschuwingen, opgelost met
  `-fps_mode cfr`.

## Stap 2: Grafana in de carrousel (nog niet gebouwd)

Grafana heeft anonymous bewust uit, omdat anders de hele historian opvraagbaar is. Die regel blijft
staan. De route:

1. Maak in Grafana een eigen gebruiker `broadcast` met de rol **Viewer**, beperkt tot één map met
   alleen de stream-dashboards.
2. Vervang in de renderer de kale Chromium door Playwright met een persistent profiel. Die logt één
   keer in via `https://milkdemo.techflow24.com/grafana/login`, met `httpCredentials` voor de basic
   auth. Daarna opent hij `https://milkdemo.techflow24.com/broadcast`. Via de publieke origin werkt
   het `Secure`/`SameSite=strict`-cookie van Grafana wél in het same-origin-iframe.
3. Zet de wachtwoorden in `.broadcast.env` (chmod 600), net als de stream key.
4. Voeg aan `screens.ts` dashboards toe als `/grafana/d/<uid>?kiosk&refresh=30s`. Kies
   dashboards met een refresh van 30 s of langer: elke refresh kost de renderer CPU.

## Stap 3: bedieningsscherm (later)

Een beheerpagina met schermvolgorde, duur, preview, streamstatus, CPU en geheugen, start/stop en
een terugvalscherm. Pas bouwen als de 24/7-stream een maand stabiel loopt.
