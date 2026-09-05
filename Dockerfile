FROM python:3.13-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Tailwind CSS standalone binary — the reason this template needs no Node and
# no bundler. See CLAUDE.md § Critical Constraints.
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "aarch64" ]; then TW_ARCH="linux-arm64"; else TW_ARCH="linux-x64"; fi && \
    curl -sL "https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-${TW_ARCH}" \
        -o /usr/local/bin/tailwindcss \
    && chmod +x /usr/local/bin/tailwindcss

# The app's dependencies, including the verification gate (ruff, mypy, pytest).
# The suite runs inside this container, so the dev tools are not optional here.
COPY src/app/requirements.txt src/app/requirements-dev.txt /tmp/app/
RUN pip install --no-cache-dir -r /tmp/app/requirements-dev.txt

# ---- app: no build step, so a restart is just a process restart ----
FROM base AS app
WORKDIR /src/app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
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
COPY src/builder/ /tmp/builder/
RUN pip install --no-cache-dir /tmp/builder/ && rm -rf /tmp/builder
WORKDIR /src
CMD ["sleep", "infinity"]
