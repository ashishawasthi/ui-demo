# Task List — DBS IDEAL Change of Account Mandate (Gemini Live copilot)

Antigravity task artifact. `[ ]` pending · `[/]` in progress · `[x]` done.

## Baseline

The journey users rely on is the **live Cloud Run build** described in `CUSTOMER_DEMO_GUIDE.md`,
which tracks `origin/main`. It covers the 13 slide-deck use cases in `tests/eval_all_usecases.py`
(change of mandate, payment prep with BEC screening, FX hedging), the START DBS JOY VOICE CALL /
END CALL bar, and the current stage defaults. Do not add, remove, or reorder user steps; put such
ideas under "Parked" for sign-off.

## P1 — Must ship together with the session-isolation change

- [ ] **Redeploy the live service.** Until the merged build is deployed, the public demo URL still
  returns the raw Gemini API key from unauthenticated `GET /api/config/live` (53-character value
  confirmed on 2026-09-20). The endpoint now returns only `api_key_configured` / `api_key_prefix`.
- [ ] **Single Cloud Run instance.** Each instance runs its own in-container database, so users can
  land on different data between requests, and per-session isolation only holds within one
  instance. Set `--max-instances=1`, not `--min-instances` (scale-to-zero preserves the
  reset-after-idle behaviour scripted demos rely on). Deploy flag only; needs the project owner.
- [ ] **CORS** defaults to credential-less `*`, correct for this app (no cookies); restrict via
  `ALLOWED_ORIGINS` for a locked-down deployment if that is ever wanted.

## P2 — Visible polish that keeps the same steps

- [ ] Toast severity styling, and keep error toasts on screen longer (messages unchanged)
- [ ] Show the governance banner only for actual governance errors (other errors currently read as
  governance violations in the same red banner)
- [ ] Reject inverted or overlapping tier bands (invalid input only; defaults and the one-click
  flow untouched)
- [ ] Human-readable timestamps and summaries in the Stage 5 audit table
- [ ] `highlight_element` targets for `SwitchActiveCustomerProfile` (`profile-{cid}`),
  `audit_board_resolution` (`board-resolution-card`), `submit_mandate_change_request` /
  `execute_cosigner_signature` (`digisign-tracker-card`), and `configure_signing_rules` when no
  tier is provided (`rule-tier-{tier_order}`) don't match any DOM id in the current build.
  `flashElement` no-ops silently, so nothing breaks, the flash animation just never fires. Either
  add the ids or point at ones that exist.

## Parked — would change the journey (needs sign-off)

- [ ] Per-session *data* isolation. Sessions now have their own active organization and stage,
  but the customer records themselves (signatories, rules, resolutions) are shared: if two people
  follow `CUSTOMER_DEMO_GUIDE.md` at the same time, the second one finds Kenneth Yap already
  revoked. Options: reseed a private copy per session, or namespace the customer tables by
  workspace id. Either is a schema change, so it needs sign-off.
- [ ] Confirmation dialogs before revoke, submit and co-sign (adds a click to scripted flows)
- [ ] Prefill the tier editor from the selected tier (breaks the one-click "Tier 1 → 150k" step)
- [ ] Stage 4 shows "Not yet audited" until an audit runs (changes what Stage 4 first shows)
- [ ] "Complete Co-Signer Approvals" signs all pending signers at once; re-submitting keeps
  existing signatures
- [ ] Real stage-completion state in the stepper (completed styling is positional today)
- [ ] Restore-signatory button on revoked cards (the endpoint exists)
- [ ] Persist Stage 1 selection on every checkbox change
- [ ] Draft-then-approve mode (decision on 2026-09-19: keep immediate apply)
- [ ] Exact-match revoke (the phonetic "Everton"/"Evelyn" → Evelyn Tan resolution is deliberate;
  EVAL-05 in the eval suite depends on it)

## Later — invisible, low value now

- [ ] Carry function-call context in chat history for multi-step follow-ups (today only the user
  text and Joy's final reply are remembered; the tab-switch fast path is not recorded at all)
- [ ] macOS/Homebrew path in `scripts/ensure_postgres.sh` (Debian/apt-get only; works fine in a
  Cloudtop-style Linux dev environment, which is what it's written for)

## Done

- [x] Per-session `X-Workspace-Id` isolation across every REST endpoint, `/ws/live`, and the
  tab-switch tool (`switch_workspace_tab` previously updated `workspace_id = 'DEFAULT'`, a row that
  does not exist, so the stage it chose was never persisted)
- [x] Apply-once `ui_sync` (stable `event_id`, deduplicated client-side)
- [x] Chat memory per session, composer lock while Joy replies, Stage 5 activity toggle
- [x] Raw Gemini API key no longer exposed via `/api/config/live`
- [x] A failed model call on the WebSocket `text_turn` path answers in-chat instead of dropping
  the socket
- [x] Switching organization resets the Stage 3 simulator result; `/api/health` reports the calling
  session's active customer
- [x] Direct browser-to-Gemini voice path dropped in favour of the backend bridge (same defect
  class origin removed in `075eaf1`); origin's barge-in, key circuit breaker, Secret Manager key
  hot-swap, AudioWorklet capture, and thread-safe broadcaster adopted as-is
