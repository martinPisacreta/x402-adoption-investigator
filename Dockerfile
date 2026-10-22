# x402 Adoption Investigator. Sample mode runs inside the container with no network, no env file, no keys:
#   docker build -t x402-investigator .
#   docker run --rm x402-investigator                                    # sample mode, example 01
#   docker run --rm x402-investigator --sample --input examples/ex3.json # another example
#   docker run --rm --env-file .env.local --add-host=host.docker.internal:host-gateway \
#       x402-investigator --mode live --input examples/ex1.json          # live mode (secrets via env only)
#   docker run --rm -p 8080:8080 x402-investigator --serve 8080 --host 0.0.0.0   # HTTP (binds loopback without --host)
# No GPU, no pip dependencies (Python 3.11 standard library). .dockerignore keeps .env*, logs/ and memory/state/ out.
FROM python:3.11-slim
WORKDIR /app
COPY . /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN mkdir -p /app/logs /app/memory/state
ENTRYPOINT ["python", "run.py"]
CMD ["--sample", "--input", "examples/01-seller-audit-textokit/input.json"]
