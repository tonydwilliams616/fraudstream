# -----------------------------------------------------------------------------
# docker/pipeline.Dockerfile - the image for the training pipeline's own steps.
#
# What: the fraudstream package with its training libraries and the AWS SDK. The
#       SageMaker Pipeline runs its prepare, evaluate and register steps in it:
#         python -m fraudstream.pipeline prepare|evaluate|register
#       (the train step runs in SageMaker's own XGBoost container instead).
# Why it differs from the app images:
#   - x86 (linux/amd64), not ARM: SageMaker Processing runs on x86 instances
#     (ml.m5). CI builds it on an x86 runner.
#   - Runs as root. SageMaker mounts the step's input and output folders under
#     /opt/ml/processing owned by root, and a non-root user can't write the
#     outputs. The container runs as a short batch job with no inbound network,
#     inside SageMaker, under the pipeline's own IAM role - not a long-running
#     service - which is why this is an accepted trade-off here.
# Same as the app images: Debian 13 (trixie) with the latest OS security patches,
#   two-stage build, Trivy-scanned in CI before it can be pushed.
# -----------------------------------------------------------------------------
FROM python:3.12-slim-trixie AS build

WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install ".[pipeline]"


FROM python:3.12-slim-trixie

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Latest OS security patches, then remove the package lists to keep it small.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /install /usr/local

ENTRYPOINT ["python", "-m", "fraudstream.pipeline"]
