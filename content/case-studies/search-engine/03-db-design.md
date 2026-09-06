# Module 03 — Database Design & Scaling

- **Inverted index (per shard):** `term -> [(doc_id, positions, offline_score)]` postings, term-hash-sharded as described above.
- **Document metadata store:** `doc_id -> {url, title, crawl_timestamp, snippet_source}` — separate from the postings themselves, since metadata is looked up once per RESULT (a handful of rows) while postings are scanned per MATCHING document (potentially millions) — different access pattern, same reasoning [`ecommerce-schema-worked-example.md`](../../database-design/ecommerce-schema-worked-example.md) uses to split `inventory` from `products`.
- **Crawl frontier and dedup store:** owned entirely by the crawler tier, cross-ref [Web Crawler](../web-crawler/README.md) — not part of the serving path at all.
- **Replication:** each shard is replicated (cross-ref [Database Replication & Failover](../../database-design/db-replication-failover.md)) so a single node failure doesn't take an entire term-range out of the index; the coordinator routes a shard's query to any healthy replica.
