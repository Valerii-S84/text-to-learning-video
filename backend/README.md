# Text to Learning Video — local media generator

Set `OPENAI_API_KEY` in `.env`. From this directory, pass approved one-line text, a local
background image, and an output name to the CLI:

```powershell
python -m anna_backend.cli.make_video `
  --text-file input.txt `
  --topic "Beim Bäcker bestellen" `
  --image resources\images\Anna.png `
  --name lesson-name
```

GUI and CLI support German (`de`, default), English (`en`), Ukrainian (`uk`),
Polish (`pl`), and Dutch (`nl`). German is the default, not a restriction.
For a non-German CLI run, pass both `--language` and `--instructions`, for example:

```powershell
python -m anna_backend.cli.make_video `
  --text "Today we learn how to ask for directions." `
  --topic "Asking for directions" `
  --image resources\images\Anna.png `
  --name directions-en `
  --language en `
  --instructions "Speak naturally in English, warmly, calmly, and clearly."
```

The desktop GUI starts through `START_ADMIN.vbs` in the repository root.
It provides editable language instructions, voice and speaking-role selection,
speech speed control, and a karaoke toggle. See the root README for setup and GUI use.

One run performs at most one TTS generation and at most two Whisper timing strategies. Alignment
below 97% fails clearly. Temporary source audio, WAV, and ASS files stay under `tmp/`;
validated artifacts are published only to `ready-audio/` and `ready-videos/`.

```text
CLI composition root
        ↓
LocalPipeline → application-owned Protocols
        ↑
OpenAI / FFmpeg / ASS adapters

DeterministicTextAligner remains synchronous application logic.
```
