# Frozen local media fixture

`sample.json` contains approved text and `sample.mp3` is its frozen provider-free audio input.
`test_local_pipeline_e2e.py` replaces only paid TTS/Whisper calls with this fixture while using
the real alignment, pacing, ASS, FFmpeg render, ready publication, and FFprobe validator path.
