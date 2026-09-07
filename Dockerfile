FROM python:3.13-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Tailwind CSS standalone binary — the reason this template needs no Node.
#
# Pinned AND checksum-verified. `latest` meant every downstream app's build
# depended on whatever shipped that morning: not reproducible, and an
# unverified binary pulled over the network into every image. A pin alone fixes
# reproducibility; the checksum is what makes the fetch trustworthy.
#
# To update: bump the version, then re-run
#   curl -sL <url> | sha256sum
# for both architectures and replace the digests below.
ARG TAILWIND_VERSION=v4.3.3
ARG TAILWIND_SHA256_X64=dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a
ARG TAILWIND_SHA256_ARM64=55fd0b241214eff3de1e8ee4f22796662f2d2e7a49bcfca7477cfd0bac398195
RUN set -eux; \
    case "$(uname -m)" in \
        aarch64) tw_arch="linux-arm64"; tw_sha="${TAILWIND_SHA256_ARM64}" ;; \
        x86_64)  tw_arch="linux-x64";   tw_sha="${TAILWIND_SHA256_X64}"   ;; \
        *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;; \
    esac; \
    curl -fsSL \
        "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-${tw_arch}" \
        -o /usr/local/bin/tailwindcss; \
    echo "${tw_sha}  /usr/local/bin/tailwindcss" | sha256sum -c -; \
    chmod +x /usr/local/bin/tailwindcss

# The dev tools are installed on purpose: scripts/verify — this project's gate,
# and its replacement for a compiler — runs ruff, mypy and pytest INSIDE this
# container. Removing them would mean the thing that gets verified is not the
# thing that runs. See docs/DECISIONS.md § 19.
COPY src/app/requirements.txt src/app/requirements-dev.txt /tmp/app/
RUN pip install --no-cache-dir -r /tmp/app/requirements-dev.txt && rm -rf /tmp/app

# No process here needs root. A numeric USER rather than a created account, so
# the image cannot fail to build over a UID that already exists — Python needs
# no passwd entry. docker-compose overrides this with the HOST user's ids, which
# is what makes writes to the bind-mounted ./src, ./data and ./logs work on
# Linux; a mismatch there turns every scaffold into a permission error.
ENV HOME=/tmp
USER 1000:1000

# ---- app: no build step, so a restart is just a process restart ----
FROM base AS app
WORKDIR /src/app
USER root
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
USER 1000:1000
EXPOSE 8080
CMD ["/entrypoint.sh"]

# ---- builder: the bb CLI, in a container that stays up so we can exec into
# it. Kept separate from `app` so `docker compose restart app` cannot kill a
# scaffold mid-write.
#
# Templates are baked into the installed package HERE, at image build time.
# After editing anything under src/builder/, run
# `docker compose up -d --build builder` — a plain restart reruns the old code
# and silently generates old-shape files.
FROM base AS builder-cli
USER root
COPY src/builder/ /tmp/builder/
RUN pip install --no-cache-dir /tmp/builder/ && rm -rf /tmp/builder
USER 1000:1000
WORKDIR /src
CMD ["sleep", "infinity"]
