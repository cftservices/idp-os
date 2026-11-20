# Renderer voor de DairyWorks-livestream: een virtueel scherm (Xvfb), een
# kiosk-Chromium die /broadcast van vla-ui toont, en ffmpeg die dat scherm
# opneemt en naar YouTube stuurt. Geen GPU: WebGL draait op de CPU
# (SwiftShader). Zie ../README.md.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    TZ=Europe/Amsterdam

RUN apt-get update && apt-get install -y --no-install-recommends \
      chromium \
      ffmpeg \
      xvfb \
      x11-utils \
      fonts-inter \
      fonts-dejavu-core \
      fonts-liberation \
      fonts-noto-color-emoji \
      ca-certificates \
      curl \
      procps \
      tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1001 streamer

# Lettertype: zonder alias wordt sans-serif Liberation Mono (zie fonts.conf).
COPY fonts.conf /etc/fonts/local.conf
# Geen vertaalbalk ("Dutch / English") in beeld: --disable-features=Translate
# houdt hem niet tegen, een beheerd beleid wel.
COPY chromium-policy.json /etc/chromium/policies/managed/broadcast.json
RUN fc-cache -f

WORKDIR /streamer
# Geen COPY --chmod: de VPS heeft geen buildx, en de klassieke builder kent het niet.
COPY start.sh /streamer/start.sh
RUN chmod 0755 /streamer/start.sh

# Chromium heeft --no-sandbox nodig in een container zonder extra rechten;
# daarom in elk geval niet als root.
USER streamer

CMD ["/streamer/start.sh"]
