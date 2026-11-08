# -----------------------------------------------------------------------------
# docker/consumer.Dockerfile - the container image the consumer runs in.
#
# What: installs the fraudstream package with its streaming extras into a slim
#       Python image and starts `python -m fraudstream.consumer`.
# Why it's built this way: the same hardened pattern as the simulator image
#   (docker/simulator.Dockerfile has the reasoning for each point):
#   - two stages, so build leftovers never reach the image that runs
#   - only the core package + the `stream` extra (Kafka client, MSK IAM signer,
#     boto3) - no training libraries; scoring happens on the SageMaker endpoint
#   - Debian 13 (trixie) with the latest OS security patches applied at build
#   - unprivileged user (uid 10001); /tmp is the only writable path at run time
#   - ARM64 for Fargate on Graviton
#   - scanned by Trivy in CI before it can be pushed (.github/workflows/images.yml)
# Settings come from environment variables at run time (see
# src/fraudstream/consumer/__main__.py); nothing environment-specific is baked in.
# -----------------------------------------------------------------------------
FROM python:3.12-slim-trixie AS build

WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install ".[stream]"


FROM python:3.12-slim-trixie

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Latest OS security patches, then remove the package lists to keep it small.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
COPY --from=build /install /usr/local

# /tmp is the only writable place at run time: ECS mounts a scratch volume
# there and the root filesystem is read-only. Declaring it a VOLUME makes
# Fargate copy the image's /tmp - including its world-writable 1777
# permissions - into that volume. Without it the volume is root-owned 0755
# and the unprivileged user can't write its dataset cache.
VOLUME ["/tmp"]

USER 10001
WORKDIR /home/app

# Exec form: Python is process 1 and receives ECS's SIGTERM directly, so the
# consumer can checkpoint, commit and leave its group cleanly.
ENTRYPOINT ["python", "-m", "fraudstream.consumer"]
