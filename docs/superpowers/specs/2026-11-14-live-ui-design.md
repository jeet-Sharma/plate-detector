# Design: Plate Stream, the live web UI

Date: 2026-11-14. Extends [the recognition design](2026-11-11-plate-recognition-design.md).
REQUIREMENTS.md listed a UI as out of scope (C5). The user has since asked for one, so it is
built as an optional layer (`ui/`). The command-line tool is unchanged and does not import it.

Method: the six steps the user asked for, in order. Steps 3 to 5 changed the plan; the
changes are marked **[changed]**.

---

## 1. Plan

### 1.1 Product surface and the user's job

- **Surface:** one local web page, opened by `python -m ui`. No installer, no account.
- **Job:** "Give it a dashcam clip, watch it work, and trust what it found."
- **Who looks at it:** an interviewer or reviewer, seconds after launch, plus a developer
  who wants to see why a plate was read the way it was.
- **Primary action:** drop a video (or press a sample clip). Everything else follows.
- **What they scan, in order:**
  1. Is it working? (live frame, progress)
  2. What did it find? (plate list)
  3. Is it right? (the crop beside the text, confidence per character)
  4. Is it fast and light? (latency per component, memory against the 512 MB budget)

### 1.2 Screen map (desktop, 1100 px and wider)

```
+--------------------------------------------------------------------------------+
| Plate Stream     [ Analyzing ]   preset [balanced v]   offline   RAM 312/512 MB |
+---------------------------------------------------------+----------------------+
|  LIVE ANALYSIS (16:9)                                   | PLATES FOUND  (5)    |
|  current frame, plate boxes, text labels                | +------------------+ |
|  top-left: 00:37.2  tiled pass     top-right: LIVE      | | thumb  9KTA262   | |
|                                                         | | conf bar  High   | |
|  (empty state: drop zone + sample clips)                | +------------------+ |
|                                                         | | thumb  7ZZV186   | |
+---------------------------------------------------------+ | ...              | |
| Upload   ########-- 76%    Analysis ####------ 41%  ETA |+----------------------+
| timeline with plate pins                                | LIVE OCR STREAM      |
+---------------------------------------------------------+ 00:12.2 777V1B6 41%  |
| Pipeline latency (ms): decode | detect | OCR | track     | 00:12.0 74ZV1B6 22%  |
| real-time factor | frames analysed / skipped | memory    | ...                  |
+---------------------------------------------------------+----------------------+
```

Under 1000 px the right column moves below the stage.

### 1.3 Interactions

- Drag and drop anywhere on the stage, or a Choose file button (keyboard reachable).
- Sample clips found next to the program appear as chips: one press starts a run.
- **Streaming:** the file goes up in 4 MB chunks while the server already analyses what has
  arrived. Both progress bars move at once.
- Plate cards appear the moment a plate passes the acceptance filter, and update in place as
  votes refine the text (changed characters flash).
- Click or press Enter on a card to open its evidence: up to four crops with the raw text
  read from each.
- Hover a pin on the timeline to see the plate; click it to jump to that card.
- Cancel at any time. Download JSON and CSV when finished. Analyse another.

### 1.4 States (trigger, visible feedback, safe next action)

| State | Trigger | What the user sees | Next action |
|---|---|---|---|
| Idle | page load | drop zone, sample chips, "stays on this computer" | choose a clip |
| Uploading + analysing | file chosen | file name, both progress bars, live frame appears within about a second | cancel |
| Partial | plates found, not finished | cards fill in; header says "Analysing, plates so far" | wait, cancel |
| Done | pipeline finished | summary strip: plates, time, peak memory; export buttons | download, analyse another |
| Empty result | finished, nothing accepted | "No readable plates found" plus what was tried | try another clip, lower threshold |
| Busy | second job while one runs | "Another clip is being analysed" | wait |
| Error | unreadable file, decode failure, server error | specific message, what failed, retry | choose another clip |
| Disconnected | event stream drops | banner "Reconnecting", then resumes from the last event | automatic |
| Cancelled | user cancels | partial plates kept, marked "stopped early" | analyse another |

### 1.5 Architecture

```
browser                                   server (stdlib, 127.0.0.1 only)
  PUT chunks ------------------------->  GrowingFile  (temp file + condition variable)
  POST /api/jobs ---------------------->  Job thread -> pipeline.run(source=GrowingFile)
  EventSource  <--- Server-Sent Events -  Job.log + latest frame  <- observer callbacks
```

- **GrowingFile:** a seekable reader over a file that is still arriving. Reads past the
  received bytes wait for more. Seeking to the end uses the size declared up front. A clip
  with its index at the start (both supplied clips) therefore starts decoding after the
  first megabytes. A clip with its index at the end simply waits for the full upload.
- **Events:** `start`, `frame` (960 px JPEG plus boxes), `reading` (crop plus text and
  per-character probabilities), `plates` (current list), `stats` (latency, memory), `done`,
  `error`, `cancelled`.
- **No new dependencies:** `http.server`, `threading`, `json`. Front end is plain HTML, CSS and
  JavaScript, no build step, no network requests (system fonts only).

### 1.6 Detail decisions

- Typography: system UI font for text; a monospace stack for plate characters and numbers,
  with tabular figures so values do not jitter.
- Plate chip: warm white with deep blue characters and a thin red top line, the look of a
  California plate. It is the one saturated object on a dark page, so the eye lands on results.
- Confidence is shown as a number and a word (High, Check, Low), and per character as
  underline height, not only as colour.
- Motion: only transform and opacity, 120 to 350 ms, none when the user prefers reduced motion.

---

## 2. Reason

Why each major choice, with the principle and the product reason.

| Choice | Principle | Reason here |
|---|---|---|
| Three regions: live view, findings, metrics | Hick's law, Miller's 7 plus or minus 2 | Four things to scan, each in its own place, nothing hidden behind tabs |
| Drop zone fills the stage when idle | Fitts's law, Jakob's law | Biggest target for the only action; familiar upload pattern |
| Respond within 100 ms to a drop, first frame within about 1 s | Doherty threshold | A silent pause reads as "broken" in a demo |
| Two progress bars with percentages and ETA | Goal-gradient effect | Visible progress keeps people watching a one-minute job |
| New plate cards pop in with a brief highlight | Von Restorff effect, reward feedback | The moment of discovery is the product's value; honest, because each card is a real result |
| Strong end state: summary plus export | Peak-end rule | The last thing seen is a clear, successful summary |
| Evidence crop beside every plate | Trust through transparency | A reviewer can check the text against the picture in one glance |
| Live OCR stream beside the voted plates | Explainability | Shows that many noisy reads become one answer, which is the core idea of the tracker |
| Latency and memory tiles | The user's stated priorities: efficiency, speed, accuracy | They are the evaluation criteria, so they are on screen, not in a log |
| Dark theme by default | Reduces glare, video content stands out | Used for watching footage; a light theme follows the system setting |

---

## 3. Replan

First plan, then what changed.

- **[changed] Playback.** First plan: show the uploaded file in a `<video>` element.
  Browsers often cannot play HEVC, and playback position would drift from analysis position.
  Now: the server sends the frames it actually analysed as small JPEGs, drawn on a canvas.
  This always works and shows exactly what the models saw.
- **[changed] Transport.** First plan: WebSocket. Needs a dependency for no gain, since traffic
  is one-directional. Now: Server-Sent Events, built into browsers and the standard library,
  with automatic reconnect and resume.
- **[changed] Parallelism.** First plan: upload fully, then analyse. The brief asks for streaming
  with results in parallel. Now: `GrowingFile`, so analysis starts while bytes arrive.
- **[changed] Card identity.** First plan: key cards by plate text. Text changes as votes
  refine. Now: key by track id, animate character changes.
- **[changed] Frame traffic.** First plan: send every frame at full size. Now: 960 px JPEG at
  quality 70 (about 60 KB), only frames that went through the models, and the server keeps
  only the newest unsent frame so a slow browser never builds a backlog.

---

## 4. Critique

Hard questions asked of the replanned design, and the fixes.

1. **Does the UI undercut the project's main claim (512 MB)?** JPEG encoding and the event log
   use memory. Fix: one 960 px encode per processed frame, crops capped at four per plate
   and 160 px wide (about 3 KB), event log capped at 5000 entries, one job at a time. Peak
   memory is measured with the UI running and reported in the README.
2. **Is a green "High" label honest?** Confidence is not a calibrated probability. Fix: the word
   bands are described as "agreement of the OCR readings", the number is always shown beside
   them, and the evidence crops let a person verify.
3. **Colour-only encoding fails for colour-blind users (about 8% of men).** Fix: every
   confidence state also has a word and an icon shape, and character confidence also changes
   underline height.
4. **Live updates can overwhelm screen reader users.** Fix: one polite live region announces
   only new plates, not every frame or reading.
5. **A fancy UI can hide a slow pipeline.** Fix: latency per component is on screen and
   comes from the same measurements as the command-line report.
6. **Local server exposed to the network?** Fix: binds to 127.0.0.1 only; job ids are random;
   sample names are checked against the directory listing; uploaded names are sanitised;
   no cross-origin access.
7. **Reconnect loses state.** Fix: the server keeps the event log and honours `Last-Event-ID`.
8. **Empty screen on first load looks unfinished.** Fix: the drop zone is designed as the
   first-class screen, with sample clips one press away.

---

## 5. Analyse

- **Success measures:**
  - First live frame within 2 s of choosing a sample on this machine.
  - First plate card within the time the pipeline finds one (clip 2: about 2 s of video).
  - A new viewer can answer the four scan questions in 5 s (checked by reading the first
    screenshot with no explanation).
- **Risks and mitigations:**
  - PyAV reading a growing file: test with a real clip fed in small pieces.
  - Thread safety of event delivery: one condition variable per job; events are append-only.
  - Large uploads: chunked, sequential, resumable by offset.
- **Verification plan:** unit tests for `GrowingFile`, the job log, and the HTTP routes with a
  tiny generated video; an end-to-end test on the first seconds of a real clip; a scripted
  browser run (system Chrome through Playwright) that uploads a sample, waits for a plate card
  and takes screenshots in dark and light themes and at phone width.

---

## 6. Colour psychology

| Role | Colour (dark theme) | Why |
|---|---|---|
| Page background | graphite `#0B0F14` | Neutral and low-glare; video and results carry the colour. Slightly cool, but not blue enough to fight the blue-tinted dashcam footage |
| Surfaces | `#121821` and `#19212C` | Layers by lightness, so structure needs few borders |
| Text | `#E8EEF5`, secondary `#9AA8BA` | High contrast (secondary about 7:1 on the page) for long sessions |
| Accent, "system is working" | teal `#2BD4C0` | Calm and technical; suggests clarity and trust without the corporate-blue cliché; used for the primary button, active progress and the scan line |
| High confidence | green `#4ADE80` | Verified, safe; green reinforces success |
| Check | amber `#FBBF24` | Attention without alarm: "look at this one" |
| Low | coral `#F87171` | Uncertain; warm, but softer than pure red so it informs rather than scolds |
| Plate chip | warm white `#F4F1E8`, deep blue `#12307A`, red line `#C8102E` | Recognisable as a plate; the brightest object on screen, so it draws the eye first |

Rules:

- No purple gradients, no decorative blobs, no cards inside cards.
- At most one accent hue (teal) plus the three meaning colours; the plate chip is an object,
  not an accent.
- Every text colour meets 4.5:1 against its background; large plate characters exceed 7:1.
- Light theme (follows the system setting) keeps the same roles with darker teal `#0E8F82`
  and white surfaces.

---

## 7. What changed while building it

Found by running the page in a real browser, not by reading code.

| Plan | Built | Why |
|---|---|---|
| FFmpeg reads the upload like a normal seekable file | An index-first MP4 reports "not seekable" while still arriving | FFmpeg jumps to the end of the media data after reading the index, which waited for the entire upload, so nothing was analysed until it finished |
| Events encoded as plain JSON | Plus a fallback that turns numpy numbers into plain numbers | A numpy number in a frame event made the event stream fail and reconnect forever; frames never reached the page. A test now encodes every event from a real clip |
| Video area as wide as its column | Always 16:9, sized from the window height | Under a flex layout it was squashed into a wide letterbox |
| Four stat tiles under the latency list | Tiles beside the list (two columns) | Saves about 70 px, so the video can be larger on a 1440x900 screen |
| Drop zone inside a 16:9 box on phones | Free height while idle | Content was clipped at 390 px wide |
| Browser `error` events parsed as data | Ignored when they carry no data | The browser raises its own `error` event when a stream closes |

Measured with the UI open: a whole clip peaked at 393 to 399 MB (command line: 348 to 368 MB).
