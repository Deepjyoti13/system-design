# Design a Web Crawler

![Web crawler pipeline: per-domain politeness queues, Bloom-filter dedup, and the content store](diagrams/hld.svg)

## Requirements

**Functional:** given a set of seed URLs, discover and fetch pages, extract outbound links, and keep expanding the frontier — without re-fetching a URL already crawled, and respecting each site's `robots.txt` and crawl-delay.

**Non-functional** (stated as assumptions, interview-style): crawl on the order of 1 billion pages/month; never send enough concurrent requests to one domain to look like an attack (politeness); recrawl popular, fast-changing pages more often than an obscure page that hasn't changed in years (freshness).

## The core design tension

A crawler isn't hard because fetching a page is hard — it's hard because of two things that only show up at scale.

**Politeness vs. throughput.** There's no shortage of URLs to fetch, but there is a hard ceiling on how fast you can hit any *one* domain before you're indistinguishable from a denial-of-service attempt. The naive fix — one global queue, drained as fast as workers can pull from it — fails immediately: nothing stops ten workers from all popping URLs on the same domain in the same second. The actual mechanism is a queue *per domain*, each with its own crawl-delay, and a frontier that round-robins across domains so throughput comes from breadth (thousands of domains in flight at once) rather than from hammering any single one. This is exactly [Rate Limiting](../../hld-building-blocks/rate-limiting.md)'s token-bucket idea, just keyed by domain instead of by client.

**Duplicate detection at scale.** With billions of URLs discovered, "have I already crawled this?" can't be a row lookup against a table with billions of rows on every single link extracted from every single page — that's the busiest, most latency-sensitive check in the whole system. The fix is [a Bloom filter](../../scalability-resilience/bloom-filters.md) as the first gate: if it says "definitely not seen," skip the real lookup entirely and queue the URL immediately. Only a "probably seen" result — a small fraction of checks — falls through to an actual store to confirm. The false-positive cost (occasionally treating a new URL as already-seen and skipping it) is cheap; the false-negative cost would be crawling the same page over and over, and a Bloom filter never produces one.

## Architecture

- **URL Frontier** — not one queue but many: a priority queue per domain, ordered by politeness delay (don't pop before crawl-delay has elapsed) and recrawl priority (see below).
- **Fetcher pool** — stateless workers pulling ready URLs from the frontier, fetching `robots.txt` once per domain and caching the result, then fetching the page itself.
- **Parser / link extractor** — pulls outbound links from fetched HTML, normalizes them (resolving relative URLs, stripping session-id query params that would otherwise multiply one page into thousands of "distinct" URLs), and hands each candidate to the seen-URL check.
- **Seen-URL Bloom filter + store** — the dedup gate described above.
- **Content store** — crawled HTML/assets are large blobs with no query pattern beyond "fetch by URL," which is exactly the shape [Object / Blob Storage](../../scalability-resilience/object-blob-storage.md) already argues doesn't belong in a primary database.

## Freshness vs. breadth

Recrawling a popular page often (freshness) and discovering brand-new pages (breadth) compete for the same finite fetcher capacity — spend it all on recrawls and you stop growing the index; spend it all on discovery and your index goes stale. Real crawlers don't pick one: the frontier's priority score blends both signals (a page's historical change-frequency and its estimated importance) so a fetcher pulling "the next most valuable URL" is naturally balancing the two instead of choosing between them.

## Interviewer follow-ups

**How would you avoid a crawler trap — an infinite URL space like a calendar page that links to "next month" forever?**
Cap total pages fetched per domain, and cap URL-path depth; a domain that keeps producing new-looking URLs past either limit gets throttled hard rather than crawled to exhaustion.

**How would you detect near-duplicate content, not just identical URLs?**
Hash page content (a shingling/minhash-style fingerprint, not a raw byte hash, so minor differences like a timestamp in the footer don't defeat it) and compare against recently-seen fingerprints — same "cheap first check, expensive confirm" shape as the URL Bloom filter.

**How would you prioritize what to crawl first out of a frontier holding billions of URLs?**
Score by a combination of estimated page importance (inbound link count is the classic cheap proxy) and domain-level trust, and always let a domain's own politeness delay gate *when* its queued URLs can be popped, regardless of score.

**What happens if `robots.txt` itself is temporarily unreachable?**
Treat it as "disallow crawling this domain for now" rather than "allow everything" — failing closed on a policy file is safer than guessing, and the domain simply gets retried later.
