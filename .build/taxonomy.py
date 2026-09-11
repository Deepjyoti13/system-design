"""Full taxonomy for the System Design Field Guide (originally 70 topics;
grew via `hld-building-blocks-gaps.md`'s research-backed additions).

Each category has an id/label/color-token and a list of items.
Item kinds:
  - "single": one markdown page. Default. File path is derived from
    category (see build_field_guide.py: folder-style categories get
    content/<cat>/<slug>/README.md, flat categories get
    content/<cat>/<slug>.md). Local images referenced in the markdown
    (e.g. ![...](diagrams/foo.png)) are resolved relative to that file
    and inlined as base64 at build time -- no Artifact publish needed.
  - "topic-urlshortener": the one pre-existing multi-tab case study,
    reusing the root-level 01-04-fundamentals.md files as-is.
  - "topic-module6": the Real-World Deep Dives shape (6 tabs:
    00-overview / 01-architecture-hld / 02-lld / 03-db-design /
    04-interviewer-qna / README), at a root-level folder named `dir`.
  - "topic-casestudy": the Case Studies tabbed shape (5 tabs:
    00-overview / 01-architecture-hld / 02-lld / 03-db-design /
    04-interviewer-qna), at content/<cat>/<slug>/ like a folder-style
    "single" -- used for a starred case study once it's been split out
    of one 8-section README.md into separate tab files.
  - "topic-tabs": a generic tabbed topic for any one-off multi-tab
    shape that doesn't fit topic-casestudy or topic-module6. The item
    itself declares `"tabs": [(fname, label), ...]` -- no new
    build-script code needed. Files live at content/<cat>/<slug>/.

`star` marks the 11 topics written to full interview depth. Ten of them
use "topic-casestudy"; the url-shortener one predates this scheme and
keeps its own bespoke 4-tab shape via "topic-urlshortener".
"""

CATEGORIES = [
    {
        "id": "foundations",
        "label": "Foundations",
        "color": "var(--accent)",
        "blurb": "The vocabulary and physics of distributed systems: how clients and servers talk, which API style to reach for, and the hard limits latency and the CAP theorem put on every design.",
        "items": [
            {"slug": "client-server-model", "label": "The Client-Server Model"},
            {"slug": "stateless-vs-stateful", "label": "Stateless vs Stateful Services"},
            {"slug": "sync-vs-async-communication", "label": "Synchronous vs Asynchronous Communication"},
            {"slug": "rest-vs-rpc-vs-graphql", "label": "REST vs RPC vs GraphQL"},
            {"slug": "latency-throughput-cap", "label": "Latency, Throughput & the CAP Theorem"},
            {"slug": "back-of-envelope-estimation", "label": "Back-of-the-Envelope Estimation"},
        ],
    },
    {
        "id": "hld-building-blocks",
        "label": "HLD Building Blocks",
        "color": "var(--accent-2)",
        "blurb": "The reusable components every high-level design leans on: load balancers, caches, CDNs, queues, gateways, sharding and replication.",
        "items": [
            {"slug": "load-balancing", "label": "Load Balancing"},
            {"slug": "caching-strategies", "label": "Caching Strategies"},
            {"slug": "cdn", "label": "Content Delivery Networks (CDN)"},
            {"slug": "forward-reverse-proxies", "label": "Forward & Reverse Proxies"},
            {"slug": "api-gateway", "label": "API Gateway"},
            {"slug": "message-queues-pubsub", "label": "Message Queues & Pub/Sub"},
            {"slug": "kafka-distributed-log", "label": "Kafka & the Distributed Log"},
            {"slug": "rate-limiting", "label": "Rate Limiting"},
            {"slug": "consistent-hashing", "label": "Consistent Hashing"},
            {"slug": "geospatial-indexing", "label": "Geospatial Indexing (Geohash, Quadtree, S2, H3)"},
            {"slug": "data-partitioning-sharding", "label": "Data Partitioning & Sharding"},
            {"slug": "replication-consensus", "label": "Replication & Consensus"},
            {"slug": "consistency-models", "label": "Consistency Models"},
            {"slug": "distributed-transactions-saga", "label": "Distributed Transactions: 2PC, Saga & Compensation"},
            {"slug": "transactional-outbox-cdc", "label": "The Transactional Outbox & Change Data Capture"},
            {"slug": "authn-authz-hld", "label": "AuthN & AuthZ at the HLD Layer"},
            {"slug": "dns-global-traffic", "label": "DNS, Anycast & Global Traffic Management"},
        ],
    },
    {
        "id": "database-design",
        "label": "Database Design",
        "color": "var(--cat-green)",
        "blurb": "Choosing and shaping the storage layer: SQL vs NoSQL, indexing, transactions, schema design, and keeping data alive when a node fails.",
        "items": [
            {"slug": "sql-vs-nosql", "label": "SQL vs NoSQL"},
            {"slug": "database-indexing", "label": "Database Indexing"},
            {"slug": "acid-vs-base", "label": "ACID vs BASE"},
            {"slug": "optimistic-vs-pessimistic-locking", "label": "Optimistic vs Pessimistic Concurrency Control"},
            {"slug": "normalization-schema-design", "label": "Normalization & Schema Design"},
            {"slug": "ecommerce-schema-worked-example", "label": "Worked Example: An E-Commerce Schema"},
            {"slug": "db-replication-failover", "label": "Database Replication & Failover"},
        ],
    },
    {
        "id": "scalability-resilience",
        "label": "Scalability & Resilience",
        "color": "var(--cat-orange)",
        "blurb": "Battle-tested techniques for keeping a system alive and fast under real failure and scale: breaking cascades, finding services, staying idempotent, and locking, searching and observing across all of it.",
        "items": [
            {"slug": "circuit-breakers-retries", "label": "Circuit Breakers & Retries"},
            {"slug": "service-discovery", "label": "Service Discovery"},
            {"slug": "idempotency-keys", "label": "Idempotency Keys"},
            {"slug": "bloom-filters", "label": "Bloom Filters"},
            {"slug": "distributed-locks", "label": "Distributed Locks"},
            {"slug": "logs-metrics-tracing", "label": "Logs, Metrics & Distributed Tracing"},
            {"slug": "object-blob-storage", "label": "Object / Blob Storage & Large Uploads"},
            {"slug": "long-polling-websockets-sse", "label": "Long Polling, WebSockets & SSE"},
            {"slug": "search-inverted-indexes", "label": "Search & Inverted Indexes"},
            {"slug": "backpressure-load-shedding", "label": "Backpressure, Load Shedding & Bulkheads"},
        ],
    },
    {
        "id": "low-level-design",
        "label": "Low-Level Design",
        "color": "var(--cat-violet)",
        "blurb": "Zooming in from boxes-and-arrows to classes and interfaces: SOLID, design patterns, UML, and two fully worked LLD examples.",
        "items": [
            {"slug": "solid-principles", "label": "SOLID Principles for System Design"},
            {"slug": "design-patterns-in-system-design", "label": "Design Patterns in System Design", "star": True, "kind": "topic-tabs", "tabs": [
                ("00-overview.md", "Overview"),
                ("01-singleton.md", "Singleton"),
                ("02-builder.md", "Builder"),
                ("03-factory-method.md", "Factory Method"),
                ("04-abstract-factory.md", "Abstract Factory"),
                ("05-adapter.md", "Adapter"),
                ("06-decorator.md", "Decorator"),
                ("07-facade.md", "Facade"),
                ("08-proxy.md", "Proxy"),
                ("09-composite.md", "Composite"),
                ("10-strategy.md", "Strategy"),
                ("11-observer.md", "Observer"),
                ("12-state.md", "State"),
                ("13-command.md", "Command"),
                ("14-template-method.md", "Template Method"),
                ("15-chain-of-responsibility.md", "Chain of Responsibility"),
                ("16-interviewer-qna.md", "Interviewer Q&A"),
            ]},
            {"slug": "uml-class-diagrams", "label": "UML & Class Diagram Basics"},
            {"slug": "lld-parking-lot", "label": "LLD Worked Example: Parking Lot System"},
            {"slug": "lld-rate-limiter", "label": "LLD Worked Example: Rate Limiter"},
            {"slug": "lld-splitwise", "label": "LLD Worked Example: Splitwise (Expense Splitting)"},
        ],
    },
    {
        "id": "case-studies",
        "label": "Case Studies",
        "color": "var(--cat-rose)",
        "blurb": "Full system designs end to end — requirements, HLD, LLD and schema together — the way an interview or a real project actually unfolds.",
        "folder_style": True,
        "items": [
            # Two URL-shortener entries on purpose, and they are different documents:
            # the "fundamentals" one is the guide's teaching walkthrough (root-level
            # 01-04 files) that introduces HLD/LLD/DB one layer at a time; the
            # case study below is the interview-depth design, with short-code
            # generation and collision avoidance promoted to its own 6th tab.
            {"slug": "url-shortener-fundamentals", "label": "URL Shortener: The Three-Level Walkthrough", "star": True, "kind": "topic-urlshortener"},
            {"slug": "url-shortener", "label": "Design a URL Shortener", "star": True, "kind": "topic-tabs",
             "lede": "The code is the primary key, and you have to mint it before you know whether it's free. Everything else follows from that.",
             "tabs": [
                 ("00-overview.md", "Overview"),
                 ("01-architecture-hld.md", "Architecture & HLD"),
                 ("02-short-code-generation.md", "Code Generation & Collisions"),
                 ("03-lld.md", "LLD"),
                 ("04-db-design.md", "DB Design"),
                 ("05-interviewer-qna.md", "Interviewer Q&A"),
             ]},
            {"slug": "object-storage-s3", "label": "Design an Object Storage Service (S3)", "star": True, "kind": "topic-tabs",
             "lede": "Eleven nines of durability out of drives that fail 0.81% of the time — and the repair-time parameter most write-ups leave out.",
             "tabs": [
                 ("00-overview.md", "Overview"),
                 ("01-architecture-hld.md", "Architecture & HLD"),
                 ("02-durability.md", "Durability & Erasure Coding"),
                 ("03-lld.md", "LLD"),
                 ("04-db-design.md", "DB Design"),
                 ("05-interviewer-qna.md", "Interviewer Q&A"),
             ]},
            {"slug": "distributed-message-queue", "label": "Design a Distributed Message Queue (Kafka)", "star": True, "kind": "topic-tabs",
             "lede": "Sequential disk is 244x faster than random, and removing per-message delivery state is what buys it. Seven segments, because this one earns them.",
             "tabs": [
                 ("00-overview.md", "Overview"),
                 ("01-architecture-hld.md", "Architecture & HLD"),
                 ("02-storage-engine.md", "Storage Engine"),
                 ("03-replication-isr.md", "Replication & ISR"),
                 ("04-consumers-delivery.md", "Consumers & Delivery"),
                 ("05-state-metadata-storage.md", "State & Metadata"),
                 ("06-interviewer-qna.md", "Interviewer Q&A"),
             ]},
            {"slug": "digital-wallet", "label": "Design a Digital Wallet", "star": True, "kind": "topic-tabs",
             "lede": "Two balances on two shards that must change together, and an audit trail that can reproduce any balance from any afternoon.",
             "tabs": [
                 ("00-overview.md", "Overview"),
                 ("01-architecture-hld.md", "Architecture & HLD"),
                 ("02-distributed-transactions.md", "2PC, TC/C & Saga"),
                 ("03-event-sourcing-cqrs.md", "Event Sourcing & CQRS"),
                 ("04-lld.md", "LLD"),
                 ("05-db-design.md", "DB Design"),
                 ("06-interviewer-qna.md", "Interviewer Q&A"),
             ]},
            {"slug": "stock-exchange", "label": "Design a Stock Exchange", "star": True, "kind": "topic-tabs",
             "lede": "One same-rack network hop costs more than the entire latency budget — so the whole critical path runs in one thread on one box.",
             "tabs": [
                 ("00-overview.md", "Overview"),
                 ("01-architecture-hld.md", "Architecture & HLD"),
                 ("02-matching-engine.md", "The Matching Engine"),
                 ("03-latency-determinism.md", "Latency & Determinism"),
                 ("04-market-data-ha.md", "Market Data & HA"),
                 ("05-db-design.md", "DB Design"),
                 ("06-interviewer-qna.md", "Interviewer Q&A"),
             ]},
            {"slug": "google-maps", "label": "Design Google Maps", "star": True, "kind": "topic-tabs",
             "lede": "Three problems in one product: 60 PB of tiles that need a CDN, a 10 GB graph that needs a search strategy, and 694k GPS samples a second.",
             "tabs": [
                 ("00-overview.md", "Overview"),
                 ("01-architecture-hld.md", "Architecture & HLD"),
                 ("02-map-rendering.md", "Map Rendering"),
                 ("03-navigation.md", "Navigation & Routing"),
                 ("04-db-design.md", "DB Design"),
                 ("05-interviewer-qna.md", "Interviewer Q&A"),
             ]},
            {"slug": "hotel-reservation", "label": "Design a Hotel Reservation System", "star": True, "kind": "topic-tabs",
             "lede": "Three reservations a second, and the entire difficulty is two people clicking Book on the last room at the same instant.",
             "tabs": [
                 ("00-overview.md", "Overview"),
                 ("01-architecture-hld.md", "Architecture & HLD"),
                 ("02-concurrency.md", "Concurrency & Double Booking"),
                 ("03-db-design.md", "DB Design"),
                 ("04-interviewer-qna.md", "Interviewer Q&A"),
             ]},
            {"slug": "distributed-email", "label": "Design a Distributed Email Service (Gmail)", "star": True, "kind": "topic-tabs",
             "lede": "A federated protocol from 1982, 40 billion recipient copies a day from 10 billion emails, and getting mail past a spam filter.",
             "tabs": [
                 ("00-overview.md", "Overview"),
                 ("01-architecture-hld.md", "Architecture & HLD"),
                 ("02-search.md", "Search"),
                 ("03-deliverability.md", "Deliverability"),
                 ("04-db-design.md", "DB Design"),
                 ("05-interviewer-qna.md", "Interviewer Q&A"),
             ]},
            {"slug": "nearby-friends", "label": "Design Nearby Friends (Live Location Sharing)", "star": True, "kind": "topic-casestudy"},
            {"slug": "chat-messaging-system", "label": "Design a Chat / Messaging System", "star": True, "kind": "topic-casestudy"},
            {"slug": "news-feed-system", "label": "Design a News Feed System", "star": True, "kind": "topic-casestudy"},
            {"slug": "distributed-rate-limiter", "label": "Design a Distributed Rate Limiter", "star": True, "kind": "topic-casestudy"},
            {"slug": "ride-sharing-system", "label": "Design a Ride-Sharing System", "star": True, "kind": "topic-casestudy"},
            {"slug": "payments-system", "label": "Design a Payments System", "star": True, "kind": "topic-casestudy"},
            {"slug": "web-crawler", "label": "Design a Web Crawler", "star": True, "kind": "topic-casestudy"},
            {"slug": "notification-system", "label": "Design a Notification System", "star": True, "kind": "topic-casestudy"},
            {"slug": "unique-id-generator", "label": "Design a Unique ID Generator", "star": True, "kind": "topic-casestudy"},
            {"slug": "distributed-key-value-store", "label": "Design a Distributed Key-Value Store", "star": True, "kind": "topic-casestudy"},
            {"slug": "google-docs-collab-editing", "label": "Design Google Docs (Real-Time Collaborative Editing)", "star": True, "kind": "topic-casestudy"},
            {"slug": "video-streaming-service", "label": "Design a Video Streaming Service", "star": True, "kind": "topic-casestudy"},
            {"slug": "ticket-booking-system", "label": "Design a Ticket Booking System", "star": True, "kind": "topic-casestudy"},
            {"slug": "dropbox-google-drive", "label": "Design Dropbox / Google Drive", "star": True, "kind": "topic-casestudy"},
            {"slug": "search-autocomplete", "label": "Design Search Autocomplete", "star": True, "kind": "topic-casestudy"},
            {"slug": "distributed-cache", "label": "Design a Distributed Cache", "star": True, "kind": "topic-casestudy"},
            {"slug": "twitter-x", "label": "Design Twitter / X", "star": True, "kind": "topic-casestudy"},
            {"slug": "instagram", "label": "Design Instagram", "star": True, "kind": "topic-casestudy"},
            {"slug": "ecommerce-platform", "label": "Design an E-commerce Platform", "star": True, "kind": "topic-casestudy"},
            {"slug": "flash-sale-system", "label": "Design a Flash Sale System", "star": True, "kind": "topic-casestudy"},
            {"slug": "top-k-frequent-visitors", "label": "Find the Top K Most Frequent Visitors in a Billion-Row Log", "star": True, "kind": "topic-casestudy"},
            {"slug": "search-engine", "label": "Design a Search Engine", "star": True, "kind": "topic-casestudy"},
            {"slug": "ad-click-aggregation", "label": "Design an Ad Click Aggregation Pipeline", "star": True, "kind": "topic-casestudy"},
            {"slug": "distributed-job-scheduler", "label": "Design a Distributed Job Scheduler", "star": True, "kind": "topic-casestudy"},
            {"slug": "real-time-leaderboard", "label": "Design a Real-Time Leaderboard", "star": True, "kind": "topic-casestudy"},
            {"slug": "proximity-service", "label": "Design a Proximity Service (Nearby Places)", "star": True, "kind": "topic-casestudy"},
            {"slug": "metrics-monitoring-observability", "label": "Design a Metrics & Monitoring System (Observability Platform)", "star": True, "kind": "topic-casestudy"},
            {"slug": "ad-server-targeting", "label": "Design an Ad Server (Targeting & Frequency Capping)", "star": True, "kind": "topic-casestudy"},
            {"slug": "webhook-delivery-system", "label": "Design a Webhook Delivery System", "star": True, "kind": "topic-casestudy"},
            {"slug": "distributed-coordination-service", "label": "Design a Distributed Lock / Coordination Service (Chubby/ZooKeeper)", "star": True, "kind": "topic-casestudy"},
            {"slug": "multiplayer-game-matchmaking", "label": "Design a Multiplayer Game Backend & Matchmaking (Online Chess)", "star": True, "kind": "topic-casestudy"},
            {"slug": "ab-testing-platform", "label": "Design an A/B Testing / Experimentation Platform", "star": True, "kind": "topic-casestudy"},
            {"slug": "online-judge", "label": "Design an Online Judge (Code Execution Platform)", "star": True, "kind": "topic-casestudy"},
            {"slug": "distributed-denylist", "label": "Design a Distributed IP/URL Denylist System", "star": True, "kind": "topic-casestudy"},
        ],
    },
    {
        "id": "real-world-deep-dives",
        "label": "Real-World Deep Dives",
        "color": "var(--cat-amber)",
        "blurb": "Narrow, mechanism-level dives into how production systems actually implement one specific tricky behavior.",
        "items": [
            {"slug": "ephemeral-content-stories", "label": "Ephemeral Content: How Stories Disappear After 24 Hours", "kind": "topic-module6", "dir": "ephemeral-content-stories"},
            {"slug": "read-receipts-presence", "label": 'Read Receipts & "Online Now" at Scale', "kind": "topic-module6", "dir": "read-receipts-presence"},
            {"slug": "push-notifications-fanout", "label": "Push Notifications: One Event, Millions of Phones", "kind": "topic-module6", "dir": "push-notifications-fanout"},
            {"slug": "like-counting-at-scale", "label": "Counting a Billion Likes Without a Billion Row Locks", "kind": "topic-module6", "dir": "like-counting-at-scale"},
            {"slug": "feature-flags-rollout", "label": "Feature Flags: Shipping to 1% Before 100%", "kind": "topic-module6", "dir": "feature-flags-rollout"},
            {"slug": "cache-invalidation", "label": "Cache Invalidation: Purging Content From Everywhere at Once", "kind": "topic-module6", "dir": "cache-invalidation"},
            {"slug": "fraud-detection-latency", "label": "Catching Fraud in the Time It Takes to Approve a Payment", "kind": "topic-module6", "dir": "fraud-detection-latency"},
            {"slug": "zero-downtime-deploys", "label": "Deploying Without Dropping a Single Request", "kind": "topic-module6", "dir": "zero-downtime-deploys"},
            {"slug": "distributed-tracing-microservices", "label": "Following One Request Across Twenty Microservices", "kind": "topic-module6", "dir": "distributed-tracing-microservices"},
        ],
    },
    {
        "id": "interview-playbook",
        "label": "Interview Playbook",
        "color": "var(--cat-slate)",
        "blurb": "How to run the 45 minutes: the framework for gathering requirements, and the trade-off reasoning that separates a strong answer from a memorized one.",
        "items": [
            {"slug": "how-to-approach-a-system-design-interview", "label": "How to Approach a System Design Interview"},
            {"slug": "common-tradeoffs-and-pitfalls", "label": "Common Trade-offs & Pitfalls"},
        ],
    },
]
