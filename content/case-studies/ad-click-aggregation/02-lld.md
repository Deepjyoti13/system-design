# Module 02 — Low-Level Design

**Window assignment and watermark-triggered finalization**, pseudocode:
```
StreamProcessor.onEvent(click):
    if DedupCache.seen(click.click_id):
        return   # already counted, ignore
    DedupCache.markSeen(click.click_id)   # atomic check-and-set

    window = tumblingWindowFor(click.client_timestamp, size=1_minute)
    RunningCounts[click.ad_id][window].increment()

    watermark = max(watermark, click.client_timestamp - allowedLateness)
    for w in windowsEndingBefore(watermark):
        if not w.finalized:
            AggregateStore.write(w.ad_id, w, RunningCounts[w.ad_id][w])
            w.finalized = True
```
A late event arriving for an already-finalized window is routed to a separate correction record rather than silently mutating a count an advertiser may have already been billed against — the pipeline makes the correction visible instead of quietly changing history.
