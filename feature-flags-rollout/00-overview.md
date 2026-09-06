# Module 00 — Feature Overview

## What this feature actually is, in plain language

A switch a PM or engineer flips from a dashboard, with no code deploy, that controls:

1. **"Turn this feature off for everyone, right now"** — a kill switch, for when something's on fire.
2. **"Show this to 1% of users, then 10%, then 100%"** — a gradual rollout, where the SAME user keeps seeing the SAME version every time they load the app, not a coin flip on every request.
3. **"Show this only to internal employees and our 500 beta testers, regardless of the percentage"** — explicit targeting that overrides the rollout math.

The part that makes this an interesting systems problem rather than a UI problem: this check runs on nearly every request, for nearly every feature, across every service in the company — often dozens of flag checks per single page load. That volume is the whole design problem: **how do you check "is this on?" thousands of times a second per server, everywhere, without that check ever becoming a network call.**

## Why this is a genuinely different problem from a typical CRUD read

Almost everything else in this system design guide's case studies is "a request comes in, go fetch some data to answer it." This feature inverts the read:write ratio to an extreme most systems never see: a flag might be edited a few times a day by a human, and evaluated tens of millions of times a second across every running instance of every service. Optimizing the READ path here doesn't mean "add a cache in front of a database" — it means making sure the read never touches the network, the database, or even a remote cache at all. That single constraint is why this design centers on push-based config distribution to an in-process evaluation library, not a request/response "flag service."
