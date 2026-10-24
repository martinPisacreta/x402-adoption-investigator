# x402 Adoption Investigator. Sample mode runs inside the container with no network, no env file, no keys:
#   docker build -t x402-investigator .
#   docker run --rm x402-investigator                                    # sample mode, example 01
#   docker run --rm x402-investigator --sample --input examples/ex3.json # another example
# Live mode from a container: docker-compose.yml (profile live) sets the host URLs and mounts the runtime's token:
#   docker compose --profile live run --rm --service-ports investigator-live --input examples/ex1.json
# or by hand (inside a container 127.0.0.1 is the container itself; the proxy-token file is mounted read-only):
#   docker run --rm --add-host=host.docker.internal:host-gateway \
#       -e NIGHTSHIFT_BASE_URL=http://host.docker.internal:8088/v1 -e NIGHTSHIFT_TOKEN_FILE=/run/secrets/proxy-token \
#       -e METERLESS_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b -v "<stateDir>/proxy-token:/run/secrets/proxy-token:ro" \
#       x402-investigator --mode live --input examples/ex1.json
#   (<stateDir>: %LOCALAPPDATA%\ai.meterless.runtime on Windows. Zetaris from a container also needs
#   ZETARIS_BASE_URL=http://host.docker.internal:3000 and a published artifact-server port, as the compose service sets.)
#   docker run --rm -p 8080:8080 x402-investigator --serve 8080 --host 0.0.0.0   # HTTP (binds loopback without --host)
# No GPU, no pip dependencies (Python 3.11 standard library). .dockerignore keeps .env*, logs/ and memory/state/ out.
FROM python:3.11-slim
WORKDIR /app
COPY . /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN mkdir -p /app/logs /app/memory/state
ENTRYPOINT ["python", "run.py"]
CMD ["--sample", "--input", "examples/01-seller-audit-textokit/input.json"]
