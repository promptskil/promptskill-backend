# Typing Debt — deferred to post-Phase-10 cleanup

Mypy is currently **non-blocking** in CI (`continue-on-error: true` in
`.github/workflows/ci.yml`). It still runs on every commit so debt stays
visible — it just does not gate merges.

This is a deliberate, time-bounded loosening per the deployment-session
trade-off: ship the backend, then dedicate a session to tightening types.

---

## Why deferred (decided 2026-04-15)

The codebase accumulated 98 mypy errors across 9 files because mypy was
added to CI in Phase 9.3 without a prior typing pass through Phases 1–9.
Of those 98, only one cluster (Pattern 6 below — Anthropic
`ContentBlock` union) had a real runtime consequence. That one is fixed.
The remaining six clusters are typing-hygiene debt with no production
impact — fixing them blocks deployment by ~45–60 minutes for cosmetic
gain.

**Trade made:** unblock Track B (Upstash → Sentry → Railway → live
deploy) → schedule typing pass after Phase 10 ships.

---

## Pattern 6 — FIXED

`anthropic.types.ContentBlock` is an 11-member union (`TextBlock`,
`ThinkingBlock`, `ToolUseBlock`, `RedactedThinkingBlock`, ...). Calling
`.text` directly on `response.content[0]` is a runtime crash whenever
extended thinking or tools are active.

- `app/services/generate_service.py:143` — narrowed via
  `isinstance(b, anthropic.types.TextBlock)`; raises
  `RuntimeError("anthropic_response_missing_text_block")` if no text
  block is present.
- `app/tasks/generate_task.py:106` — same fix.

---

## Patterns 1–5, 7 — DEFERRED

### Pattern 1: `**dict[str, object]` to `sentry_sdk.init` (~60 errors)

- `app/main.py:46`
- `app/tasks/email_task.py:102`

`init_kwargs` is built as a dict literal with mixed value types, then
splat-unpacked. Mypy infers `dict[str, object]` and rejects every
keyword argument.

**Fix when revisiting:** annotate `init_kwargs: dict[str, Any]`, OR
call `sentry_sdk.init(...)` with positional kwargs instead of `**`.

---

### Pattern 2: `sentry_sdk = None` reassignment (3 errors)

- `app/tasks/email_task.py:47`
- `app/tasks/purge_task.py:41`
- `app/tasks/generate_task.py:47`

`import sentry_sdk` types as `Module`; the fallback `= None` requires
`Optional[ModuleType]`.

**Fix when revisiting:**

```python
from types import ModuleType
sentry_sdk: ModuleType | None
try:
    import sentry_sdk as _sentry
    sentry_sdk = _sentry
except ImportError:
    sentry_sdk = None
```

---

### Pattern 3: `integrations.append(...)` mixed types (2 errors)

- `app/main.py:31`
- `app/tasks/email_task.py:86`

List inferred from first element (e.g. `CeleryIntegration`); appending
a different integration class fails.

**Fix when revisiting:** declare `integrations: list[Integration] = []`.

---

### Pattern 4: `Settings()` missing args (3 errors)

- `app/config.py:22`

pydantic-settings reads from environment at runtime; mypy treats every
field as required at construction.

**Fix when revisiting:** either add `# type: ignore[call-arg]` on the
`Settings()` line, or use the pydantic-settings mypy plugin.

---

### Pattern 5: SQLAlchemy `Column[X]` vs `X` (7 errors)

- `app/services/auth_service.py:98, 108, 125`
- `app/services/user_service.py:74`
- `app/services/history_service.py:63, 167`

SA's ORM attributes type as `Column[X]` at the class level but `X` at
the instance level; without the SA mypy plugin, mypy can't tell which
context it's in.

**Fix when revisiting:** add to `pyproject.toml`:

```toml
[tool.mypy]
plugins = ["sqlalchemy.ext.mypy.plugin"]
```

Then re-run mypy and patch the residual sites with `cast(...)` or
explicit annotations on the model attributes.

---

### Pattern 7: slowapi exception handler signature (2 errors)

- `app/main.py:61, 64`

Starlette's `add_exception_handler` types its second arg as
`Callable[[Request, Exception], Response | Awaitable[Response]]`;
slowapi's handler types its arg as the narrower `RateLimitExceeded`.
This is variance — handlers SHOULD accept narrower exception types,
but Starlette's signature doesn't model that.

**Fix when revisiting:** add `# type: ignore[arg-type]` on both
`add_exception_handler` calls. Document why above each.

---

## Re-tightening checklist (post-Phase-10 cleanup session)

- [ ] Apply Pattern 1 fix (annotate `init_kwargs: dict[str, Any]`)
- [ ] Apply Pattern 2 fix (`Optional[ModuleType]`)
- [ ] Apply Pattern 3 fix (`list[Integration]`)
- [ ] Apply Pattern 4 fix (`# type: ignore[call-arg]` on `Settings()`)
- [ ] Enable SA mypy plugin in `pyproject.toml`
- [ ] Apply Pattern 5 residual fixes after plugin runs
- [ ] Apply Pattern 7 fix (`# type: ignore[arg-type]`)
- [ ] Run `mypy app/ --ignore-missing-imports` — expect 0 errors
- [ ] Remove `continue-on-error: true` from `.github/workflows/ci.yml`
- [ ] Push, confirm CI mypy step is now blocking and green
