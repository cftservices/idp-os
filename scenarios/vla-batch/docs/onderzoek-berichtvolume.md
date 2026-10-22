# Openstaand: het berichtvolume van het park

> Status op 2026-08-09: **niet opgelost.** Twee hypotheses zijn hard uitgesloten,
> de derde is aangewezen maar niet bewezen. Dit moet af vóór de volgende uitrol,
> want bij twaalf machines bepaalt het of het park op de VPS past.

## De waarneming

Gemeten op de VPS met `park-slim` (vier machines, waarvan twee via OPC UA):

```
raw/vla-park/#         6,0 msg/s     klopt met het budget
DairyWorks/Vla-B/#   196,2 msg/s     ~4x te hoog, budget was ~50
```

Per topic uitgesplitst over 12 seconden:

```
326  DairyWorks/Vla-B/pasteuriser-01/Ch1.Dev1.TT_2049_PV     = 27 Hz
280  DairyWorks/Vla-B/pasteuriser-01/Ch1.Dev1.TT_2042_PV
258  DairyWorks/Vla-B/pasteuriser-01/Ch1.Dev1.TT_2056_PV
223  DairyWorks/Vla-B/separator-01/Machine.Process.Rpm
212  DairyWorks/Vla-B/separator-01/Machine.Process.FatPct
```

`TT_2049_PV` is `temp_out_C`, sampling-klasse `fast`, dus **1 Hz** volgens het
model. Gemeten: 27 Hz.

Let op dat deze topics onder `DairyWorks/` staan. Dat was een tweede, losstaande
fout (device-namespace) en is inmiddels gecorrigeerd naar `raw/vla-park`, met een
gate in `tools/check-generated.sh`. Het volume-probleem staat daar los van.

## Hypothese 1: dubbele registraties — UITGESLOTEN

`init-park.sh` is drie keer gedraaid terwijl het "bestaat al"-patroon nog kapot
was, dus dubbel toegevoegde adressen waren aannemelijk. Gemeten in
`idp.deviceconfigs`:

```
intake-silo-01    60 adressen, unieke 60, DUBBEL 0
separator-01      30 adressen, unieke 30, DUBBEL 0
pasteuriser-01    60 adressen, unieke 60, DUBBEL 0
```

60 = 30 signalen + 30 `.Q`-companions voor vendor-a; 30 voor vendor-b, die geen
companions heeft. Precies zoals bedoeld, geen duplicaten.

## Hypothese 2: OPC-UA samplingInterval — UITGESLOTEN

MonsterMQ zet `subscriptionSamplingInterval: 1000`, maar de per-item
`monitoringParameters.samplingInterval` staat default op `0.0`, en 0 betekent in
OPC UA "zo snel als de server aankan". Dat leek de dader.

Gemeten met een gecontroleerd experiment: de pasteur lokaal gedraaid met zijn
OPC-UA-oppervlak, tegelijk geteld hoe vaak de SIM naar een node schrijft en
hoeveel notificaties een echte asyncua-client terugkrijgt.

```
samplingInterval 0      sim 1,00/s   client 0,92/s   verhouding 0,9x
samplingInterval 1000   sim 1,00/s   client 1,00/s   verhouding 1,0x
```

Een op een, in beide gevallen. De sim schrijft niet te vaak en het protocol
meldt niet te vaak. De instelling maakt geen verschil.

Script: `scratchpad/rate_experiment.py` uit de sessie van 2026-08-09; de kern is
twee tellers over hetzelfde venster, een aan de schrijfkant en een aan de
subscription-kant.

## BEWEZEN op 2026-08-11 — lees dit eerst

Hypothese 3 klopt, en het bewijs is korter dan het onderzoek eronder. Vier
machines draaiden naast elkaar met **hetzelfde 30-slots sjabloon** maar via
verschillende transporten. Dat is een gecontroleerd experiment zonder dat je
iets hoeft te stoppen: het begrote budget is ~6 msg/s per machine.

| machine | transport | gemeten | tegen budget |
|---|---|---:|---:|
| `filler-01` | MQTT, de sim publiceert zelf | **6,5 /s** | 1,1× |
| `blend-tank-01` | Modbus via `park-poller` | 58,0 /s | 9,7× |
| `separator-01` | OPC UA via MonsterMQ | 35,5 /s | 5,9× |
| `pasteuriser-01` | OPC-DA via MonsterMQ | 125,1 /s | 20,8× |

De enige machine die zijn eigen berichten verstuurt zit **exact op het budget**.
Alles wat door een verzamelaar gaat is 6 tot 21 keer over. Het sjabloon,
de fysica en de sampling classes zijn identiek, dus die vallen af als oorzaak.

**De rokende revolver.** Eén adres van `separator-01` over 15 seconden:

```
raw/vla-park/separator-01/Machine.Process.Rpm   264 berichten, 16 verschillende waarden

{"value":5764.1,"timestamp":"2026-08-11T02:18:51.765420Z","status":0}
{"value":5764.1,"timestamp":"2026-08-11T02:18:51.765420Z","status":0}
{"value":5764.1,"timestamp":"2026-08-11T02:18:51.765420Z","status":0}
```

Niet alleen dezelfde waarde: **hetzelfde brontijdstempel**. Dat is geen nieuwe
meting, dat is dezelfde sample die opnieuw de bus op gaat. 264 publicaties op
16 echte samples is een factor 16,5. De 16 samples in 15 s kloppen precies met
de `fast`-klasse van 1 Hz, dus de sim doet het goed en de verzamelaar niet.

**Er is geen knop.** `subscriptionSamplingInterval` staat al op 1000 ms en doet
aantoonbaar niets aan het aantal publicaties. Het schema van MonsterMQ heeft per
adres maar één relevante optie, `publishMode`, en die kent alleen `SINGLE` en
`SEPARATE`: dat gaat over de vorm van het topic, niet over de frequentie. Er is
dus geen instelling die dit temt. De keuze ligt bij ons:

1. **Laten staan en het als les gebruiken.** 287 msg/s ruw tegen 11,4 canoniek is
   precies wat een naïeve verzamelaar met je bus doet, en waarom de deadband in
   de conditioner geen luxe is. TDengine merkt er niets van; alleen de broker
   betaalt (7,1% CPU van 2 vCPU bij vier machines).
2. **`RAW_PUBLISH=mqtt` op de OPC-machines.** De sim publiceert dan zelf, zoals
   `filler-01`, en je zit meteen op het budget. Kost je wel het OPC-UA-ingestpad,
   en dat is een dragend deel van het verhaal.
3. **De poller repareren.** ✅ **GEDAAN, zie hieronder.**

Voor twaalf machines is 1 alleen houdbaar als de broker het trekt; meet dat
voordat je opschaalt.

### De poller is gerepareerd (2026-08-11)

`park-poller` las elke seconde alle registers plus alle `.Q`-companions en
publiceerde alles, ongeacht de sampling class of er iets veranderd was. Dat is
geen kapotte poller, dat is een verkeerd geconfigureerde poller, en je merkt het
alleen als je telt.

`Schedule` in `poller.py` doet nu wat het sjabloon al zei:

| klasse | gedrag |
|---|---|
| fast / normal / slow | één bericht per 1 / 5 / 30 s |
| onchange | bij elke wijziging, plus een hartslag van 60 s |
| `.Q`-companion | bij elke kwaliteitswissel meteen, plus een hartslag van 30 s |

Twee grenzen zijn met opzet blijven staan. **Dit is geen deadband**: die werkt
op engineering-waarden en hoort in de conditioner, en die scheiding is precies
wat de demo betoogt. En **een kwaliteits- of toestandswissel wordt nooit
uitgesteld**, want anders zie je een storing pas een halve minuut later.

De klasse zelf staat nu in `park-conditioning.json` (`sampling_class`), want uit
`expected_interval_s: 60` alleen kun je "onchange met hartslag" niet
onderscheiden van "elke 60 s cyclisch". De conditioner kijkt er niet naar.

Gemeten resultaat, zelfde meting als hierboven:

| machine | transport | voor | na |
|---|---|---:|---:|
| `blend-tank-01` | Modbus via `park-poller` | 58,0 /s | **6,2 /s** |
| `filler-01` | MQTT, sim publiceert zelf | 6,5 /s | 6,2 /s |
| `separator-01` | OPC UA via MonsterMQ | 35,5 /s | 60,3 /s |
| `pasteuriser-01` | OPC-DA via MonsterMQ | 125,1 /s | 196,6 /s |

De poller zit nu **exact op de machine die zichzelf publiceert**. Dat de twee
OPC-machines ondertussen omhoog gingen bevestigt de diagnose alleen maar: hun
tempo hangt niet aan onze code maar aan de leeslus van MonsterMQ, en dat tempo
fluctueert. 95% van de resterende 269 msg/s komt van die twee.

Bewaakt door check 7 en 8 in `park-poller/selftest.py`: 7 simuleert 60 seconden
met opzettelijk vaak wisselende toestanden en faalt boven 10 msg/s, 8 controleert
dat een kwaliteits- en een toestandswissel er meteen doorheen gaan.

## Terzijde, gevonden tijdens dezelfde meting: een stille poller

`blend-tank-01` stond op **0 msg/s** en had nog nooit een byte geleverd. De
container draaide gezond, de Modbus-server luisterde keurig op 5020, en de
conditioner meldde 0 ongemapte topics. Alles groen.

De poller verbond met `blend-tank-01:5020`, maar de container heet
`vla-blend-tank-01`. De fallback in `poller.py` is het kale `equipment_id` en de
generator gaf de containernaam nooit mee. Modbus en REST pushen niet, dus een
poller die niets kan bereiken doet precies niets: geen crash, geen restart-loop,
geen ongemapt topic. **Stilte lijkt op een machine die stilstaat.**

Gerepareerd in `gen-park.py` (de compose krijgt nu `MODBUS_HOST_<X>` en
`REST_URL_<X>` uit `container_name` in het model) met gate 5 in
`check-generated.sh` eromheen, die zowel op een ontbrekende als op een verkeerde
host faalt. Het is geen toeval dat dit pas bij een volumemeting opviel: er was
geen enkel ander signaal.

## Hypothese 3: MonsterMQ's eigen OPC-UA-client — AANGEWEZEN, NIET BEWEZEN

> Achtergrond. Hierboven staat de uitkomst; hieronder staat hoe we er kwamen.

Wat overblijft. Het lokale experiment gebruikte een **asyncua**-client; MonsterMQ
gebruikt zijn eigen implementatie (Eclipse Milo, Kotlin). Als die per
sample-cyclus publiceert in plaats van per verandering, dan levert 30 tags op
1 Hz al 30 msg/s per device op, en met de `.Q`-companions het dubbele.

Dat verklaart de ORDE van grootte (114-196 msg/s over twee OPC-devices) maar
niet de 27 Hz op een enkele tag. Er ontbreekt dus nog een stap.

## Hoe je het afmaakt

Het lokale experiment kan dit niet beantwoorden: je hebt MonsterMQ zelf nodig.

1. Start **een** OPC-UA-machine plus MonsterMQ, verder niets.
2. Tel tegelijk drie dingen over hetzelfde venster: hoe vaak de sim naar de node
   schrijft, hoeveel notificaties MonsterMQ van de OPC-UA-server krijgt (uit
   `docker logs monstermq` op debug), en hoeveel MQTT-berichten er uitgaan.
3. Herhaal met `publishMode: SINGLE` in plaats van `SEPARATE` op de adressen; dat
   is de enige adres-optie in het schema die het gedrag kan verklaren.

Doe dit met EEN machine. Twaalf machines erbij zetten om een volume-vraag te
beantwoorden is dezelfde fout als deze demo elders bekritiseert.

## Wat er ondertussen geldt

De drie bevindingen uit de go/no-go van 2026-08-09 zijn wel verwerkt:
geheugenlimiet 192m voor OPC-machines (asyncua zat op 96% van 96m), een throttle
van 5 s op cross-check-delta's, en de raw-namespace. `park-slim` draaide met
0 ongemapte topics en een correct werkende Condition/Model-laag; het volume is
het enige dat een uitrol van twaalf machines nu blokkeert.
