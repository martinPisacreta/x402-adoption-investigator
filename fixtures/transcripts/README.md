# Recorded generator transcripts

`<example>/<prompt_id>-<prompt_sha12>.json` with `{prompt_id, prompt_sha256, model, recorded_at, response}` and
the prompt text beside it as `<prompt_id>-<prompt_sha12>.prompt.txt`, written by `scripts/record_transcripts.py`
from a live run (`--record-transcripts`). Sample mode replays them through `RecordedGenerator`; a miss falls back
to `NullGenerator` and a logged `recovery{reason: "generator_fallback"}`. Empty in the scaffold.
