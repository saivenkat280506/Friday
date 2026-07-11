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
