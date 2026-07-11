# FRIDAY implementation log

## Baseline — 2026-07-11

- Version: `0.1.0` (desktop and browser agent).
- Baseline commit: `c9b1e84` — `chore: baseline Friday v0.1.0`.
- Existing architecture: Next.js/Electron desktop client, FastAPI core, Groq reasoning, Pocket TTS clone at `backend/tts/voices/friday-voice.wav`, Faster-Whisper STT, Puppeteer browser sidecar, and PyAutoGUI/PyWinAuto desktop controls.
- Gaps found: STT sent audio to Groq before trying local Whisper; no separate minimized-mode overlay; terminal captures and nested dependencies were not excluded consistently.

## Change set — local-first voice and companion overlay

### Before

- Speech recognition depended on Groq first, making usage quota-dependent and sending microphone audio off-device.
- Minimizing the main app removed FRIDAY's visible presence.
- TTS was already Pocket-TTS-only with the cloned FRIDAY WAV, so no alternate speech provider was introduced.

### After

- STT is local Faster-Whisper first and only. `medium.en` is the accuracy-first default, with model/device/compute settings configurable in `.env`; use `small.en` for faster lower-spec CPU operation.
- `/health` exposes FRIDAY's actual state so companion surfaces can reflect listening, transcribing, thinking, and speaking.
- Electron creates a top-center, click-through, always-on-top FRIDAY orb when the main window is minimized. It polls the local state endpoint and animates per live state without blocking desktop interaction.
- `.gitignore` now excludes nested dependencies and terminal captures from future commits.

## Deliberate boundaries

- Pocket TTS remains the sole speech-synthesis path. The supplied `friday-voice.wav` clone remains authoritative.
- Groq remains the reasoning brain. It is no longer required for STT.
- Browser control remains in the existing Puppeteer sidecar; native application control remains in the existing guarded PyAutoGUI/PyWinAuto executor. These capability paths are intentionally separate because browser DOM control and Windows UI automation require different interfaces.

## Change set — minimized companion visual redesign

### Before

- The minimized companion was an 80px square containing a continuously pulsing orb and a small label.
- Its motion was visible even when FRIDAY was idle, which made it look like a blinking error indicator rather than a calm desktop assistant.

### After

- The minimized companion is a compact, top-center dark glass card inspired by the supplied reference: a framed violet assistant orb, F.R.I.D.A.Y. branding, live state indicator, status copy, and a small audio meter.
- Idle mode is intentionally still. Motion appears only while FRIDAY is listening, thinking, transcribing, or speaking; reduced-motion preferences disable all effects.
- The card remains click-through and always-on-top, so it does not block work on the desktop.

## Change set — synchronized companion motion

### Before

- The card had an external drop shadow and diffuse background glow that visually faded into the desktop.
- It polled `/health` on a timer, so its animation could lag behind the main application state.

### After

- The card has a clean, shadow-free edge; only the mini orb keeps its contained violet glow.
- It connects to the same `/ws` state stream as the main application, with health checks only as a recovery fallback.
- Card movement and orb motion are synchronized with FRIDAY's live state: a 1.96s listening pulse, a slow 8.9s processing cycle, and a 1.3s speaking response rhythm. Idle remains still.

## Change set — reference-aligned compact widget

### Before

- The companion used a wide horizontal information-card layout that did not match the supplied reference widget.

### After

- The companion is a compact 222×152 black widget: violet mini-ring at top-left, waveform/microphone capsule at top-right, and a live task panel with a thin activity trace below.
- The widget has no card shadow, blur, or diffuse background glow. Its visual hierarchy, proportions, and top control arrangement now follow the supplied screenshot.

## Change set — companion controls and reliable conversation routing

### Before

- The minimized companion was click-through, so neither its microphone capsule nor task panel could be used.
- The widget's animation was only visible for backend state transitions and appeared inactive at rest.
- Headline requests could fall through to planning instead of using the headline reader. Casual jokes could drift into invented news. “From local” music requests were treated as a Spotify search.

### After

- The microphone capsule starts listening; the task panel restores the main FRIDAY window. Both controls support keyboard activation.
- The mini ring, audio bars, and activity trace have a gentle idle motion; live listening, processing, transcription, and speaking animations still follow actual backend state broadcasts.
- News requests now use the dedicated headline tool, jokes return a concise standalone joke, and local music requests search the user's Music and Downloads folders before launching the matching local audio file.
