# Module 00 — Feature Overview

**Diagram for this module:** [Ephemeral Stories — feature overview](https://claude.ai/code/artifact/f6d0cc2b-25cb-479b-9019-4141d77164b4)

## What this is, in plain language

A user posts a photo or short video. Everyone who follows them can see it — but only for 24 hours. After that, it's gone completely, even for the person who posted it (no "restore," no "recently deleted"; this is the one hard rule the whole design exists to protect). Optionally, the poster can see who viewed it, in the order they viewed it.

That's the entire feature from a user's point of view: **post → visible for exactly one day → permanently gone.** Everything in modules 01–04 is infrastructure built to make that one sentence true at massive scale without it becoming slow, expensive, or (worse) wrong — a story that's still visible after its 24 hours is a correctness bug, not a rough edge, on the same level as a bank showing the wrong balance.

## Why this is a genuinely different problem from module 01–03's URL shortener

The root project's URL shortener never deletes anything — a short link lives forever once created, so the whole design is about serving reads fast. Here, **deletion (or at least disappearance) is a first-class functional requirement**, not cleanup. That single difference is why this system needs a mechanism the URL shortener never had to think about at all: something has to *guarantee* a story stops being servable at exactly the right moment, at a scale where "just run a DELETE query" would mean scanning and deleting billions of rows a day. Module 01 spends most of its space on that one problem.

## Out of scope for this worked example

- **Story Highlights** (saving a story past 24 hours to a permanent profile section) — a real feature, but it turns "ephemeral" into "ephemeral by default, permanent on request," which is a meaningfully different (and larger) design. Worth sketching yourself once you've read 01–03, using the same three-layer method.
- **Close-friends / custom-audience story lists** — touched on as a practice extension at the end of module 02.
