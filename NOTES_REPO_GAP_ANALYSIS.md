# Gap Analysis — this repo vs. `~/Projects/system-design-notes`

**Compared:** all 28 chapters of `system-design-notes` (Alex Xu *System Design Interview* vol. 1 + 2 notes, ~52k words, image-heavy) against this repo's 289 markdown files (~280k words).

**Headline:** this repo is 5× larger and structurally far stronger (HLD/LLD/DB/Q&A per topic, explicit trade-off tables, capacity math). The notes repo wins on (a) **nine topics this repo does not have at all**, and (b) **specific mechanism-level detail** inside topics both repos cover — the kind of detail an interviewer actually pushes on.

---

## Part 1 — Chapter-by-chapter mapping

| # | Notes chapter | This repo | Verdict |
|---|---|---|---|
| 01 | Scale from Zero to Millions | scattered across `foundations/` | **No single page** — the evolution narrative (1 server → DB split → LB → replication → cache → CDN → stateless → multi-DC → queue → sharding) is a canonical opener and exists nowhere as one story |
| 02 | Back-of-the-Envelope | `foundations/back-of-envelope-estimation.md` | ✅ covered — verify powers-of-two table, Jeff Dean latency numbers, availability-nines table |
| 03 | Framework for SD Interviews | `interview-playbook/how-to-approach-a-system-design-interview.md` | ✅ covered |
| 04 | Rate Limiter | `case-studies/distributed-rate-limiter/` | ✅ this repo deeper |
| 05 | Consistent Hashing | `hld-building-blocks/consistent-hashing.md` | ✅ covered |
| 06 | Key-Value Store | `case-studies/distributed-key-value-store/` | ✅ good (Merkle/gossip/hinted-handoff present); **missing sloppy quorum, SSTable naming** |
| 07 | Unique ID Generator | `case-studies/unique-id-generator/` | ✅ this repo deeper |
| 08 | **URL Shortener** | root `01`–`03-*-fundamentals.md` only | **GAP** — the running teaching example, but *not* a case study, and the collision question is answered in one clause instead of the full hash-vs-base62 treatment |
| 09 | Web Crawler | `case-studies/web-crawler/` | ✅ this repo deeper |
| 10 | Notification System | `case-studies/notification-system/` | ✅ this repo deeper |
| 11 | News Feed | `case-studies/news-feed-system/` | ✅ this repo deeper |
| 12 | Chat System | `case-studies/chat-messaging-system/` | ✅ this repo deeper |
| 13 | Search Autocomplete | `case-studies/search-autocomplete/` | ✅ this repo deeper |
| 14 | YouTube | `case-studies/video-streaming-service/` | ✅ good; **thin on the transcoding DAG / GOP alignment / resource-manager queues** |
| 15 | Google Drive | `case-studies/dropbox-google-drive/` | ✅ good; **thin on delta sync + block-level dedup mechanics** |
| 16 | Proximity Service | `case-studies/proximity-service/` | ✅ good; **geohash boundary problem + quadtree-vs-S2 trade-off table not fully argued** |
| 17 | **Nearby Friends** | — | **GAP** — constantly-changing locations; Redis pub/sub channel-per-user at 334k updates/s |
| 18 | **Google Maps** | — | **GAP** — map tiling, routing tiles at multiple resolutions, ETA, geocoding, adaptive rerouting |
| 19 | **Distributed Message Queue** | `hld-building-blocks/kafka-distributed-log.md` | **GAP** — building block exists, but not the *design* of one: WAL segments, consumer rebalancing protocol, ISR + ack levels, delayed messages |
| 20 | Metrics Monitoring & Alerting | `case-studies/metrics-monitoring-observability/` | ✅ this repo deeper |
| 21 | Ad Click Aggregation | `case-studies/ad-click-aggregation/` | ✅ good; **watermarks / window types / Lambda-vs-Kappa / star schema under-argued** |
| 22 | **Hotel Reservation** | `case-studies/ticket-booking-system/` (adjacent) | **PARTIAL GAP** — room-*type* inventory, deliberate 10% overbooking, and the three-way pessimistic/optimistic/DB-constraint comparison are distinct from seat-level booking |
| 23 | **Distributed Email Service** | — | **GAP** — SMTP/IMAP/POP, MX records, deliverability & IP warm-up, per-user search index |
| 24 | **S3-like Object Storage** | `scalability-resilience/object-blob-storage.md` | **GAP** — building block only; missing erasure coding vs. replication, WAL packing of small objects, multipart upload, GC compaction |
| 25 | Real-time Leaderboard | `case-studies/real-time-leaderboard/` | ✅ good; **skip-list internals + write-sharding + percentile fallback thin** |
| 26 | Payment System | `case-studies/payments-system/` | ✅ this repo deeper |
| 27 | **Digital Wallet** | — | **GAP** — TC/C vs. Saga, event sourcing for auditability, Raft-replicated event log, CQRS |
| 28 | **Stock Exchange** | — | **GAP** — matching engine, order book data structure, sequencer determinism, mmap bus, multicast fairness |

---

## Part 2 — Concept pages still missing

From this repo's own `hld-building-blocks-gaps.md`, Tier 2/3 items **never written**, several of which the notes repo depends on:

Never written: `multi-region`, `multi-tenancy`, `api-design-for-scale`, `serialization-schema-evolution`, `event-sourcing-cqrs`, `leader-election-coordination`, `optimistic-vs-pessimistic-locking`, `networking-fundamentals`, `fanout-write-vs-read`, `batch-vs-stream-processing`, `oltp-vs-olap`, `autoscaling-capacity`, `geospatial-indexing`, `clocks-and-ordering`, `slis-slos-error-budgets`, `data-retention-archival`.

Concepts the notes repo uses that exist **nowhere** in this repo:
`erasure coding`, `TC/C (try-confirm-cancel)`, `sloppy quorum`, `Hilbert curve / S2`, `GOP alignment`, `delta sync`, `consumer rebalancing`, `star schema / dimensions`, `mmap event bus`, `order book`, `SSTable` (named).

---

## Part 3 — Where this repo already exceeds the notes repo

These topics have no notes-repo counterpart and are worth deepening rather than leaving as-is:

`ab-testing-platform`, `ad-server-targeting`, `distributed-cache`, `distributed-coordination-service`, `distributed-denylist`, `distributed-job-scheduler`, `ecommerce-platform`, `flash-sale-system`, `google-docs-collab-editing`, `instagram`, `multiplayer-game-matchmaking`, `online-judge`, `ride-sharing-system`, `search-engine`, `top-k-frequent-visitors`, `twitter-x`, `webhook-delivery-system`, plus the eight root-level deep dives (`cache-invalidation`, `distributed-tracing-microservices`, `ephemeral-content-stories`, `feature-flags-rollout`, `fraud-detection-latency`, `like-counting-at-scale`, `push-notifications-fanout`, `read-receipts-presence`, `zero-downtime-deploys`).

Known debt: `ab-testing-platform`, `distributed-coordination-service`, `multiplayer-game-matchmaking`, `webhook-delivery-system` have **1 of 4 diagrams** each.

---

## Part 4 — Structural note

`.build/taxonomy.py` already supports `topic-tabs`, where an item declares its own `"tabs": [(fname, label), …]`. So a topic that needs a sixth or seventh segment (e.g. Stock Exchange needs a *matching engine* segment; Object Storage needs a *durability* segment) does not have to be forced into the 5-tab shape.

---

# Part 5 — Progress log

## Batch 1 — missing case studies (DONE, prose)

All nine case studies the notes repo had and this repo lacked are written to full interview
depth. Several use **more than the standard 5 segments**, per the instruction not to force a
generic structure — the extra tab in each case is the mechanism the topic actually turns on.

| Case study | Segments | Extra segment(s) | Words |
|---|---|---|---|
| `url-shortener` | 6 | **Code Generation & Collisions** | 16.4k |
| `object-storage-s3` | 6 | **Durability & Erasure Coding** | 15.2k |
| `distributed-message-queue` | 7 | **Storage Engine**, **Replication & ISR**, **Consumers & Delivery**, **State & Metadata** | 15.3k |
| `digital-wallet` | 7 | **2PC/TC-C/Saga**, **Event Sourcing & CQRS** | 15.6k |
| `stock-exchange` | 7 | **Matching Engine**, **Latency & Determinism**, **Market Data & HA** | 16.5k |
| `google-maps` | 6 | **Map Rendering**, **Navigation & Routing** | 14.1k |
| `distributed-email` | 6 | **Search**, **Deliverability** | 13.4k |
| `hotel-reservation` | 5 | **Concurrency & Double Booking** | 11.9k |
| `nearby-friends` | 5 | — (standard shape fits) | 11.4k |

**~130k words.** Note `url-shortener` now has two taxonomy entries on purpose: the original
teaching walkthrough (root `01`–`04-*-fundamentals.md`) is relabelled
**"URL Shortener: The Three-Level Walkthrough"** under slug `url-shortener-fundamentals`,
and the new interview-depth case study takes the `url-shortener` slug.

### Concept pages added (needed by the above, and on the Tier 2/3 gap list)

- `database-design/optimistic-vs-pessimistic-locking.md` — also fixed a pre-existing broken
  link from `feature-flags-rollout` that pointed at this never-written page.
- `hld-building-blocks/geospatial-indexing.md` — geohash, quadtree, S2, H3.

### Repo hygiene fixed along the way

- **~30 pre-existing broken relative links** repo-wide, resolved by basename against real
  file locations (case studies have no `README.md`, so `../x/README.md` → `../x/00-overview.md`;
  root deep-dives are three levels up from `content/case-studies/<slug>/`, not two).
- **Anchor slugs containing `--`** (em-dashes in headings collapse to a single hyphen in
  GitHub-style slugs) — fixed repo-wide.
- Repo now builds **100 topics** (was 90).

## Item 1 — the diagram pass: DONE

**58 SVGs drawn, rendered, visually reviewed and exported.** Every `diagrams/*.svg` path
referenced by any case-study markdown file now exists; a repo-wide audit finds zero
mismatches, and `index.html` rebuilds at 17.8 MB with **259 inlined `svg-embed` blocks**.

| Case study | Diagrams |
|---|---|
| `url-shortener` | 5 / 5 |
| `object-storage-s3` | 5 / 5 |
| `distributed-message-queue` | 6 / 6 |
| `digital-wallet` | 6 / 6 |
| `stock-exchange` | 6 / 6 |
| `nearby-friends` | 4 / 4 |
| `hotel-reservation` | 4 / 4 |
| `distributed-email` | 5 / 5 |
| `google-maps` | 5 / 5 |
| `ab-testing-platform` | 4 / 4 (hld exported from its existing `.excalidraw`; lld + er new) |
| `distributed-coordination-service` | 4 / 4 (same) |
| `multiplayer-game-matchmaking` | 4 / 4 (same) |
| `webhook-delivery-system` | 4 / 4 (same) |

Notes on the four pre-existing studies: each already had a complete, good-quality
`hld.excalidraw` that had simply never been exported to SVG, so those four were exported
rather than redrawn. Their `lld` and `er` diagrams were authored from scratch.

Every diagram was built to argue rather than display — the title states the claim, and the
layout is the evidence for it. A thin Python emitter (`exc.py`, in the session scratchpad)
filled only invariant Excalidraw boilerplate; every coordinate, colour and label was passed
explicitly. Its `check()` pass flags text/text and text/shape overlaps before rendering,
which caught most layout defects without a render cycle; the rest were caught by reading
each rendered PNG and fixing what was visibly wrong.

Two defects worth recording because they were systemic rather than one-off:

- **Contained text was centre-aligned by default**, which mangles code blocks. `box()` now
  takes `align=`, and the digital-wallet ER diagram's SQL block was re-exported.
- **`table()`'s `tcol` argument is an absolute x, not an offset.** Passing an offset silently
  renders the type column outside (or to the left of) its own table.

## Still outstanding

1. **Batch 2 — remaining concept pages** from Part 2: `multi-region`, `multi-tenancy`,
   `api-design-for-scale`, `serialization-schema-evolution`, `event-sourcing-cqrs` (as a
   standalone building block — currently only inside the wallet case study),
   `leader-election-coordination`, `networking-fundamentals`, `fanout-write-vs-read`,
   `batch-vs-stream-processing`, `oltp-vs-olap`, `autoscaling-capacity`, `clocks-and-ordering`,
   `slis-slos-error-budgets`, `data-retention-archival`.
2. **Batch 3 — depth passes** on the 7 overlapping topics where the notes repo went deeper
   (Part 1's "thin on…" column): video transcoding DAG/GOP, Drive delta-sync mechanics,
   proximity geohash-vs-quadtree argument, ad-click watermarks/windowing/Lambda-vs-Kappa,
   leaderboard skip-list internals, KV-store sloppy quorum, and a standalone
   "Scale from Zero to Millions" page (notes ch. 1 has no counterpart here).
