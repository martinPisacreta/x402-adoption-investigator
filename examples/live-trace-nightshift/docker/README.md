# One Nightshift request from a container (2026-10-24)

`docker-smoke-planner-20261024/` is a copy of the run folder `scripts/nightshift_smoke.py` wrote inside a
container: one Planner request through Meterless Nightshift on the host, reached as `host.docker.internal`, answered
by NVIDIA Nemotron 3.5 Lightning 30B A3B on NVIDIA's hosted API. Nothing in it is edited by hand.

How it ran (Git Bash on the Windows demo machine, Docker Desktop 29.6.2, image `python:3.11-slim`
`sha256:cce12316475d7d56d605f76e1d811d0dc4e7227f2d4afd29951521264f504ff8`; `<ctx>` was a copy of the tracked files of
the working tree, so no `.env.local` was inside the container; `<out>` an empty scratch folder; `<token>` the
runtime's own `%LOCALAPPDATA%\ai.meterless.runtime\proxy-token`, mounted read-only and never copied into a variable):

```
MSYS_NO_PATHCONV=1 docker run --rm --add-host=host.docker.internal:host-gateway \
  -e PYTHONDONTWRITEBYTECODE=1 -e HMEM_DIR=/out/hmem-state \
  -e NIGHTSHIFT_BASE_URL=http://host.docker.internal:8088/v1 -e NIGHTSHIFT_TOKEN_FILE=/run/secrets/proxy-token \
  -e METERLESS_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b \
  -v "<ctx>:/app:ro" -v "<out>:/out" -v "<token>:/run/secrets/proxy-token:ro" -w /app python:3.11-slim \
  python scripts/nightshift_smoke.py --prompt-file fixtures/transcripts/01-seller-audit-textokit/planner-731796da80fd.prompt.txt \
    --prompt-id planner --agent planner --max-tokens 400 --run-id docker-smoke-planner-20261024 --out /out
```

What the files show:

- `run.jsonl` seq 1: `recovery{zetaris_unreachable}` (Zetaris was stopped; the smoke runs no SQL anyway). seq 2:
  `run_start` with `mounts.generator: "nightshift:nvidia/nemotron-3.5-lightning-30b-a3b"` and `generator_mount`
  (`base_url: http://host.docker.internal:8088/v1`, `found_by: NIGHTSHIFT_BASE_URL`, `timeout_s: 90.0`,
  `token_source: NIGHTSHIFT_TOKEN_FILE`, a variable name, never the token). seq 3: the `generate` event, `routing.routed_model:
  nvidia/nemotron-3.5-lightning-30b-a3b`, `winning_route.kind: cloud`, receipt `rcpt_req_6ujsjy37`, 2,103 ms, 474
  input and 180 output tokens; the reply parsed as a JSON object (the smoke checks no more than that). It proposed
  playbook `payer_dossier`, which the Planner would not adopt for this seller-audit question: the router's only
  candidate without a wallet in focus is `seller_profile`, the plan of the sample run on the same prompt
  (`examples/01-seller-audit-textokit/run.jsonl` seq 6).
- `generator.jsonl`: the same call with its parameters (max_tokens 400, temperature 0, the thinking switch
  `reasoning_effort: none` plus the `/no_think` system turn).
- `transcripts/`: the exact prompt and the reply.

This is the Nightshift path of `docker-compose.yml`'s `investigator-live` service (same variables, the token file
mounted read-only), made with the stock Python image instead of a build of this repository's image. A full live run
from the container, and Zetaris from the container, have not been run.
