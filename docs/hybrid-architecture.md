# Local hybrid: temi/gpt and stef/modelexperiment

Both branches separate frame capture, recognition jobs, and speech. This comparison is from the actual checked-out code, not branch names. `temi/gpt` remains the working branch; no partner files were changed.

| Concern | Temi implementation | Stef implementation | Hybrid choice |
|---|---|---|---|
| Proposals | YOLOE at 1280 plus LISA47 YOLOv8m; specialist can create missed proposals | YOLOE at 960 with five prompts plus HSV red/white/yellow/orange/green patch proposals | Keep specialist and current proposals; color filtering helps names but do not add more noisy proposals without validation |
| Symbol identity | LISA47 trained categories with temporal voting, CLIP local fallback for categories absent from LISA | Primarily OCR; optional cloud readers for missing meanings | Keep trained specialist and local fallback; zero cloud image calls |
| Text | Apple Vision OCR across up to three best retained crops; strict standard text rules | Apple Vision OCR plus vocabulary cleanup/edit distance and standard construction phrase completion | Port raw street-name filtering; defer speculative phrase completion until it improves held-out text results |
| Small signs | Higher image size, but still limited by source pixels | Lower image size, minimum24px crop gating; color proposals can catch larger construction boards | Temi should have better small-symbol coverage, at extra compute cost; no model restores absent text pixels |
| Repeats | Physical track identity and per-text duplicate window; different same-type signs can appear | 20-second class-wide quiet period and spatial growth/read retry heuristics | Keep per-track handling: class-wide quiet would hide the second speed 40 sign in this minute |
| Uncertainty | Demo nearest supported category, scores retained | Drops unrecognized candidates; optionally eight cloud reads | Keep guesses for relevant traffic candidates, exclude names/ads before guessing |
| Speech | Every catalog result/tentative result with persisted audio status, active-drive checks, freshness deadline | Safety-critical subset only,20-second freshness, fewer playback diagnostics | Keep current independent speech queue and ElevenLabs |

## Implemented filtering

- Before fallback classification, inspect raw OCR for a standalone street name suffix. Destination names inside `EXIT`, cardinal-direction, construction, speed, and warning messages remain relevant.
- If OCR cannot read a candidate, a small horizontal green blade can be dropped using its native detection-box aspect and green-pixel fraction. This is a conservative clip-resolution heuristic, not a comprehensive guide-sign classifier.
- Local CLIP street-name and advertising negatives compete before forcing a catalog category. A distinct negative top label with sufficient similarity margin is dropped. Blurry traffic-sign candidates continue to receive the user's requested closest-label guess.
- Removed candidates publish `detection_remove` with their reason and drive/event IDs, never enter speech, and cannot be requeued under the same track identity. Pipeline metrics expose street/business filter counts.

## Measurements and limits

`research/vision-architecture/hybrid-filter-benchmark.json` uses six independently reviewed traffic crops plus six previously reviewed hard negatives from the real video. The filter retains all six traffic crops and removes four of the six negative candidates: all three green street blades plus one business board. The previous forced nearest-label path supplied traffic categories for all six negatives. The building fascia and another business/property board remain false guesses.

This is filtering coverage, not full-drive precision/recall. The six ground-truth examples are not exhaustive. Tight-crop tests omit proposal/tracking errors. No-turn-on-red still receives the wrong nearest symbol label on its held-out native crop; the trained LISA specialist lacks that category. The second speed crop can identify the sign type without reading 40. These failures are not fixed by street filtering.

Run `PYTHONPATH=. .venv-local/bin/python scripts/benchmark_hybrid_filter.py` for crop results and `PYTHONPATH=. .venv-local/bin/python scripts/benchmark_hybrid_drive.py --baseline-ref <snapshot-commit>` for isolated real-time before/after pipeline results. Drive benchmarks disable real speech, storage writes, thumbnail writes, and browser interaction. They do not validate ElevenLabs playback.

The green heuristic may remove a distant unreadable exit/guide panel that is as small and thin as a blade. Larger guides and readable exit/direction messages are retained by regression tests; highway guide coverage has not been measured on a highway clip. Broader data and a trained street/guide/business discriminator would improve that boundary.

## Full-drive replay results

The committed baseline snapshot was `88e6a55`; both runs processed the whole 60-second clip with real-time frame sampling, independent reader and fake speech queues, and zero cloud image calls.

| Metric | Baseline | Final hybrid replay |
|---|---:|---:|
| Analyzed frames |448 |476 |
| Approximate analyzed frames/second |7.44 |7.90 |
| Median combined inference |146.1ms |120.6ms |
| Median reading job |125ms |116ms |
| Explicit street-name removals |0 |5 |
| Explicit business removals |0 |1 |
| Cloud image calls |0 |0 |

The observed latency difference cannot be attributed solely to filtering: these sequential replays differ in warmup, runtime frame selection, and GPU load. The final hybrid generated fake speech events for testing, not actual ElevenLabs playback.

Outputs still vary: an earlier hybrid replay retained both speed 40 signs, while the final replay missed the second physical speed 40 sign. School, first speed 40, construction, and the combined crossing survived the final replay. No-turn-on-red was given a wrong nearest specialist label. False guesses on fascia, plaques, and property boards still exist, and duplicate tracker IDs cause repeated guesses/audio. Filtering improves relevance; it does not make the recognizer completely correct.
