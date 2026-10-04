# Local clip demo

1. Open http://127.0.0.1:8000/ and choose Local vision. Recognition runs locally; cloud image recognition is disabled. ElevenLabs still supplies audio.
2. Enable voice output in the browser and click Start drive. This user gesture allows audio playback. The real one-minute clip is `data/drive.mp4`.
3. Watch tracked sign crops become resolved or unresolved. A proposed box is not yet a recognized sign. Read the captured text/value on resolved cards.
4. Listen for short descriptive ElevenLabs cues for resolved catalog signs, including speed limits. Street names outside the catalog may be logged without a catalog cue. Repeated sightings of one sign should not produce repeated announcements. Local readings must arrive within their relevance window; unreadable or stale crops do not speak a guessed meaning.
5. Click Park to practice resolved catalog signs captured on this drive. Pending, unresolved and uncatalogued signs do not become quiz questions. Two sightings of the same speed limit give one question; different limits remain distinct.
6. For a speed-limit question answer the actual number, such as `40` or `forty miles per hour`. Use Hear in Spanish for the captured sign's explanation.

Meanings in the catalog remain marked as drafts. This is a demonstration of recognition and language practice; detection alone does not establish which lane or road a sign applies to.

## Repeatable checks

- A school sign and speed-limit cards should produce catalog announcements when resolved, subject to configured voice policy and available/cached ElevenLabs audio.
- Parking must produce questions only from this drive's resolved catalog signs, with its saved thumbnails.
- A pending local reading must not stop detection or other signs from resolving.
- A new drive must not reuse pending results from the previous drive.

Record frame count, recognition latency, audio enqueue/playback outcomes and unresolved crops for each run. Candidate counts do not establish recognition accuracy.
