# Module 00 — Feature Overview

**Diagram for this module:** [Feature overview](https://claude.ai/code/artifact/e5a81e89-488e-4194-a7ae-596baf4b461c)

## What this feature actually is, in plain language

Two small things a chat app does constantly, and users never think about until they break:

1. **"Is Sarah online right now, or when did she last check the app?"** — a green dot vs. a "last seen" timestamp next to her name.
2. **"Did the message I sent actually get read?"** — the familiar sent → delivered → read checkmark progression under a message.

Neither one is "a database row I read." Both are *live* facts that change the instant something happens elsewhere — Sarah closes the app, or opens the chat and reads your message — and the UI has to reflect that within a second or two without the user refreshing anything. That's the whole design problem: **how do you push a fact to someone's screen the moment it changes, at a few hundred million people doing this simultaneously.**

Open the [overview diagram](https://claude.ai/code/artifact/e5a81e89-488e-4194-a7ae-596baf4b461c) — it shows exactly this, with no infrastructure in the picture yet: a message moving through Sent → Delivered → Read, and a contact's status flipping between "online now" and "last seen 2 min ago." Everything in `01`–`03` exists to make those two boxes true at scale.

## Why this is a genuinely different problem from the URL shortener

The root project's URL shortener (`../01-hld-fundamentals.md` etc.) is a **request/response** system: a client asks a question, the server answers, done. This feature is a **push** system: nobody asked a question, but the screen has to update anyway. That single difference is why this design needs a persistent connection (WebSocket) where the URL shortener needed none — it's not a "better" architecture, it's a different shape of problem requiring a different primitive. Keep that distinction in mind through `01`; a lot of "why WebSocket, not REST" answers reduce to it.
