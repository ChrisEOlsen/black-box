# Setup Checklist

## First-Time Setup
- [ ] Clone this repo
- [ ] `cp env.example .env` and set `SESSION_SECRET` (`openssl rand -hex 32`)
- [ ] Run `./install-claude.sh` (Claude Code) and/or `./install-opencode.sh` (opencode)
- [ ] `docker compose up -d --build`
- [ ] Open your AI tool in this directory
- [ ] Verify the builder answers — `./bb inspect` should print the manifest

## Before `/build`
- [ ] `SEED.md` filled in with app name, features, auth requirements
- [ ] `.env` has all required API keys for integrations checked in SEED.md
- [ ] `scripts/verify` passes on a clean checkout

## Before `/launch`
- [ ] App reviewed and working at `http://localhost:[APP_PORT]`
- [ ] `TUNNEL_TOKEN` set in `.env`
- [ ] `APP_ENV=production` (also disables `/docs`)
- [ ] Domain configured in Cloudflare dashboard (Zero Trust → Tunnels)
