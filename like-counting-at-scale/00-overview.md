# Module 00 — Feature Overview

**Diagram for this module:** [Like Toggle, Plain Sight](https://claude.ai/code/artifact/aab1ad83-46a9-4fc6-be9f-61b6167eee0e)

## The feature, with no infrastructure in it yet

A user taps the heart under a post. The heart fills in, and the number beside it goes up by one. Tap it again, the heart empties, and the number goes back down by one. That's the entire feature from the user's side — a single toggle, per person, per post, with a running total displayed next to it.

Two things about that description matter enormously for everything that follows, even though neither one is visible in the diagram:

- **The toggle is exact.** A given user has either liked this post or they haven't — there's no "maybe," no losing track of your own tap. Whatever sits behind this button has to answer "did I already like this?" correctly, every time, for every one of a platform's users against every one of its posts.
- **The number is approximate, under load.** Nobody audits whether a viral post shows 4,128,991 likes or 4,128,994 a few seconds after they tapped. What people notice is the number moving in the right direction, quickly, and never visibly wrong (never going down when they just liked something).

That asymmetry — **exact per-user state, approximate aggregate count** — is the entire reason this system is an interesting design problem instead of a single `UPDATE` statement. Module 01 picks it up from there.

## What this module deliberately leaves out

No cache, no queue, no shard, no database engine is named yet. That's the point: if you can't describe the feature in plain language first, you'll end up designing infrastructure to solve a problem you haven't actually stated. Every box that shows up starting in module 01 has to trace back to making *this* — a fast, correct-looking toggle — hold up when a post gets liked 50,000 times a second.
