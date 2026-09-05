# Module 00 — Feature Overview

**Diagram for this module:** [Push Notifications — feature overview](https://claude.ai/code/artifact/92491520-213c-4d71-a852-d9c1203ebe2d)

## What this feature actually is, to a user

Someone you follow posts something. Within seconds, your phone buzzes with a notification — even though the app isn't open. That's the whole feature, from the outside: **an event happens somewhere in the product, and it shows up on a screen you're not currently looking at.**

Three flavors of that, all going through the same pipe:

- **Transactional** — "here's your login code" — must arrive in seconds, to exactly the one device that asked.
- **Social** — "someone you follow posted" — arrives to everyone who follows them, which could be one person or fifty million.
- **Bulk/marketing** — "there's a sale today" — arrives to a large list, with no urgency at all.

The system doesn't know or care what's *inside* the notification. It only has one job: given an event and a list of people to tell, get a message onto their phone's lock screen, using whatever channel their phone actually listens on (Apple's network for iPhones, Google's for Android).

## Why this is harder than "send a push"

The naive version — loop over every device and call "send push" — breaks the moment one of these is true, all of which are true for a real product:

- The list of devices is sometimes 1 (a 2FA code) and sometimes 50 million (a celebrity's post), and both have to go through the same system.
- Apple and Google's push networks are two different services with two different APIs, rate limits, and failure modes.
- A phone that's uninstalled the app, or hasn't opened it in a year, has a dead token — sending to it forever wastes work and (at scale) trips rate limits meant for real devices.
- "The user has 3 phones" means one logical event should not become three separate, uncoordinated sends that might arrive, retry, and duplicate independently.

Module 01 designs the architecture that handles all four of those at once, at the scale where "loop over every device" stops working long before you'd expect.
