# Ops — Deployment Runbook (Phase 9.3)

This is the one-shot handoff for taking PromptSkill backend from a green CI
run to a live Railway deployment. Follow top-to-bottom. Every checkbox is
load-bearing — skipping one leaves a silent failure mode downstream.

> **Grain anchor**: Pattern (Railway auto-deploys main on push) → Problem
> (without CI gate + correct env, broken code or missing secrets ship) →
> Output (3 healthy services — web, worker, beat — with migrations applied,
> Sentry receiving, Redis reachable) → Input (env vars + git push).

---

## 1. Prerequisites

- [ ] GitHub repo on `main` branch, CI passing (`.github/workflows/ci.yml`).
- [ ] Upstash account — for Redis (Celery broker + rate-limit storage).
- [ ] Railway account — for Postgres + 3 app services.
- [ ] Sentry account — project created, DSN copied.
- [ ] Resend account — API key provisioned, `noreply@cosight-ai.com` domain verified.
- [ ] Anthropic API key.

---

## 2. Provision infrastructure

### 2.1 Upstash Redis

1. Create a new Redis database (Global region or closest to Railway region).
2. Copy the **`rediss://` connection string** (TLS-enabled). This is `REDIS_URL`.

### 2.2 Railway project

1. New project → **Deploy from GitHub repo** → select `promptskill-backend`.
2. Add a **Postgres plugin** to the project. Railway auto-injects `DATABASE_URL`
   into services that reference `${{Postgres.DATABASE_URL}}`.
3. Create **three services** from the same repo:
   - `web` — start command: blank (uses Procfile `web`)
   - `worker` — start command: blank (uses Procfile `worker`)
   - `beat` — start command: blank (uses Procfile `beat`)
4. On the **web service only**, set Pre-Deploy / Release command to
   `alembic upgrade head` (also defined in `railway.toml` for the default
   service; confirm it is applied to `web` specifically).
5. Worker and beat services: **leave release command empty** — only web
   should run migrations to avoid race conditions on multi-service deploy.

---

## 3. Environment variables — per service

Legend: **W** = web, **K** = worker, **B** = beat. `x` = set on that service.

| Variable                       | W | K | B | Source / value                                        |
|--------------------------------|---|---|---|-------------------------------------------------------|
| `DATABASE_URL`                 | x | x | x | `${{Postgres.DATABASE_URL}}` (Railway reference)      |
| `REDIS_URL`                    | x | x | x | Upstash `rediss://…` connection string                |
| `JWT_SECRET`                   | x | x | x | 32+ random bytes (`openssl rand -hex 32`)             |
| `RESEND_API_KEY`               | x | x |   | Resend dashboard → API Keys                           |
| `ANTHROPIC_API_KEY`            | x |   |   | console.anthropic.com → API keys                      |
| `SENTRY_DSN`                   | x | x | x | Sentry project settings → Client Keys (DSN)           |
| `SENTRY_ENVIRONMENT`           | x | x | x | `production`                                          |
| `SENTRY_RELEASE`               | x | x | x | Railway-provided `RAILWAY_GIT_COMMIT_SHA` reference   |
| `SENTRY_TRACES_SAMPLE_RATE`    | x | x | x | `0.1` (tune later)                                    |
| `SENTRY_PROFILES_SAMPLE_RATE`  | x | x | x | `0.1`                                                 |
| `DEBUG_SENTRY`                 |   |   |   | **Never set in prod.** Only flip on temporarily for the |
|                                |   |   |   | one-shot `/debug-sentry` smoke test, then unset.      |
| `PORT`                         | x |   |   | Injected automatically by Railway on web service      |

**Why `REDIS_URL` on all three**: web uses it for slowapi rate limiting, worker
is the Celery consumer, beat is the Celery scheduler. Missing on any one
silently degrades that service.

**Why `RESEND_API_KEY` only on web + worker**: web triggers send via the
auth service, worker owns the Celery task that actually calls Resend. Beat
never sends email directly.

**Why `ANTHROPIC_API_KEY` only on web**: calibration requests are served
in-request on the web service. Worker does not call Anthropic today.
(Revisit when async calibration lands.)

---

## 4. Deploy sequence

1. Push the merged PR to `main`.
2. **GitHub Actions** runs `ci.yml` — must be green. If branch protection
   is configured with "test" as a required check, Railway will not see
   the commit until CI passes.
3. **Railway** picks up the commit, builds via Nixpacks, and for the web
   service runs the pre-deploy `alembic upgrade head` before starting
   uvicorn.
4. Worker and beat start on their own; they do not run migrations.

Watch the Railway build logs for each service until all three show
"Deployment successful" and the web service logs a uvicorn boot line.

---

## 5. One-shot verification (do every deploy)

### 5.1 Web health
```bash
curl -s https://<web-service>.up.railway.app/health
# expect: {"status":"ok"} or equivalent
```

### 5.2 Migrations applied
Railway shell on web service:
```bash
alembic current
# expect: head revision printed, no "pending" warnings
```

### 5.3 Worker alive
Railway shell on worker service:
```bash
celery -A app.tasks.celery_app inspect ping
# expect: -> celery@<hostname>: OK   pong
```

### 5.4 Beat schedule loaded
Railway shell on beat service, tail logs:
```
beat: Scheduler: Sending due task purge_expired_reset_tokens ...
```
The `purge_expired_reset_tokens` task should appear on Mondays at 00:00 UTC.
If it does not show up in the beat log within a minute of boot, the
`beat_schedule` is misconfigured.

### 5.5 Sentry end-to-end (one-time per deploy)
1. Temporarily set `DEBUG_SENTRY=true` on the web service → redeploy.
2. `curl https://<web-service>.up.railway.app/debug-sentry` — expect 500.
3. Open Sentry dashboard → confirm the `sentry_debug_probe` event landed
   with `environment=production` and `release=<commit sha>`.
4. **Unset `DEBUG_SENTRY`** → redeploy. Verify `/debug-sentry` returns 404.
   This endpoint must never be reachable in steady-state production.

### 5.6 Rate limiter reachable
```bash
# hit signup 6 times rapidly with the same IP — expect 429 on the 6th.
for i in 1 2 3 4 5 6; do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST https://<web-service>.up.railway.app/auth/signup \
    -H "Content-Type: application/json" \
    -d '{"email":"probe+'"$i"'@example.com","password":"Xx1!xxxxx"}'
done
# expect: 200 200 200 200 200 429  (or similar — depending on slowapi limit)
```

A `500` here usually means slowapi cannot reach Redis — recheck `REDIS_URL`
on the web service.

---

## 6. Rollback

Railway keeps prior deployments. To roll back:

1. Railway → service → Deployments → select last-known-good → **Redeploy**.
2. If the rollback crosses a migration boundary, coordinate a manual
   `alembic downgrade <rev>` from the web service shell **before**
   redeploying the old code. Automatic downgrade is not wired.

---

## 7. Close-out checklist

- [ ] All three services show "Active" in Railway dashboard.
- [ ] `/health` returns 200 from the web URL.
- [ ] `celery inspect ping` returns `pong` from worker.
- [ ] Beat log shows `purge_expired_reset_tokens` in the schedule.
- [ ] Sentry received the `/debug-sentry` event with correct env + release.
- [ ] `DEBUG_SENTRY` is **unset** and `/debug-sentry` returns 404.
- [ ] Rate-limit smoke test returns a 429 after threshold.
- [ ] Mark Step 9.3 and Step 4.1b `[x]` in `build-checklist/references/checklist.md`.
