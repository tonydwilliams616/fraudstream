# -----------------------------------------------------------------------------
# docker/simulator.Dockerfile - the container image the simulator runs in.
#
# What: installs the fraudstream package with its streaming extras into a slim
#       Python image and starts `python -m fraudstream.simulator`.
# Why it's built this way:
#   - Two stages: the first installs everything, the second copies only the
#     installed result, so build leftovers (pip caches, build files) never
#     reach the image that runs.
#   - Only what the simulator needs: the core package plus the `stream` extra
#     (Kafka client, MSK IAM signer, boto3) - not the training libraries.
#   - Runs as an unprivileged user (uid 10001), never as root.
#   - Built for ARM64: Fargate on Graviton costs ~20% less than x86 for the
#     same size, and GitHub builds it natively on an ARM runner.
# Settings come from environment variables at run time (see
# src/fraudstream/simulator/__main__.py); nothing environment-specific is
# baked into the image, so the same image runs anywhere.
# -----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS build

WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install ".[stream]"


FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
COPY --from=build /install /usr/local

USER 10001
WORKDIR /home/app

# Exec form: Python is process 1 and receives ECS's SIGTERM directly, so the
# simulator can flush Kafka and exit cleanly.
ENTRYPOINT ["python", "-m", "fraudstream.simulator"]
