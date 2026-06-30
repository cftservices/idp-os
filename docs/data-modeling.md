# Data modeling — ISA-95, UNS, and the 3-layer discipline

> Step 3 (**Model**) is the heart of the layer. This is where raw tags become
> AI-ready information. Two ideas do the work: a **semantic hierarchy** (ISA-95 /
> UNS) and a **3-layer storage discipline** (staging → warehouse → marts).

## Why modeling is the hard part

A factory emits thousands of tags with names like `FT_4021_PV`. Those names mean
something to one SCADA engineer and nothing to an AI model. Modeling is the act
of giving every signal a stable, hierarchical, vendor-neutral identity — so that
a query, a dashboard, or a model can find "the outflow temperature of
Pasteurizer 1" without knowing the PLC's tag naming scheme.

Skip this and you have a data *swamp*: lots of values, no meaning. That is
exactly why most factory AI initiatives stall.

## ISA-95 hierarchy / Unified Namespace

Topics and graph nodes follow an ISA-95 equipment hierarchy
(Enterprise → Site → Area → Work Center → Work Unit), expressed as a Unified
Namespace (UNS) on the broker and as relationships in Neo4j.

```
DairyPlant/                       (Site)
├── Receiving/                    (Area)
│   └── Tank01/                   (Work Unit)
│       ├── level
│       └── temperature
├── Process/
│   ├── Separator/...
│   ├── Pasteurizer/temp_out
│   └── Homogenizer/...
└── Packaging/
    └── Bottler/...
```

The same hierarchy lives in two places, each for a reason:
- **Broker topics (UNS)** — the live, real-time view; how data moves.
- **Neo4j graph** — the durable structure; how equipment relates (P&ID topology,
  parent/child, root-cause paths). Cypher queries answer "what feeds this unit?"

## The 3-layer storage discipline

Borrowed from analytics engineering (staging → warehouse → marts) and translated
to OT. Each MongoDB collection belongs to exactly one layer, and the mutability
rule is what keeps the layer trustworthy.

| Layer | Collection prefix | Contents | May mutate? |
|-------|-------------------|----------|-------------|
| **1. Staging** | `staging.*` (1:1 per source) | Raw payloads + metadata straight from the broker, e.g. `staging.plc_siemens_line3` | ❌ Never touch raw |
| **2. Warehouse (UNS)** | `warehouse.*` | ISA-95 hierarchy + canonical signal names, e.g. `warehouse.signals`, `warehouse.assets` | ❌ Append-only |
| **3. Marts** | `marts.*` | Use-case views per business outcome, e.g. `marts.oee_line3`, `marts.energy_per_batch` | ✅ Rebuild freely |

**Why the mutability rule matters.** Raw is sacred — you can always rederive
everything from staging, so it is never edited. The warehouse is the single
canonical truth, so it is append-only. Marts are disposable opinions for a
specific question, so they can be dropped and rebuilt without fear. The discipline,
not the database size, is what makes a layer production-grade.

> v2 → v3 note: the current `plc_data` collection becomes `staging.plc_data`
> (a no-op rename); `warehouse.*` and `marts.*` are added. No breaking change for
> the Next.js dashboard.

## Tag aliasing

Legacy tag names change. A single rename upstream must never break a downstream
consumer. The cross-cutting alias table records every mapping:

```
warehouse.tag_aliases
  { legacy_tag, canonical_signal_uuid, vendor, retired_at }
```

Every legacy-tag rename in **Connect** is recorded here, so **Store →
Distribute** consumers keep resolving the canonical signal regardless of what the
PLC tag was called this year. This is also the mechanism behind the Condition-step
naming normalisation (ladder tag ↔ MQTT topic ↔ canonical signal).

## How this connects to the rest of the build

- Modeling produces the **warehouse** that everything downstream queries.
- Change review on tags/topics is a [DataOps discipline](dataops-for-ot.md) —
  one rename breaks consumers if it isn't reviewed.
- The store choice (MongoDB vs TDengine) is orthogonal to the model; the 3-layer
  pattern maps onto either — see [store-layer.md](store-layer.md).
