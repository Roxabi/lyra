# ADR-051 Rollout Evidence — #715

Captured 2026-04-22 on **roxabituwer** (M1, prod) after merging PR #880
(`feat(nats): per-identity _INBOX prefix retires bus-wide wildcard`).

## Rollout steps

1. Pulled `staging` (HEAD `fd3250c`) on M1, ran `uv sync`.
2. Restarted `lyra_hub`, `lyra_telegram`, `lyra_discord` via supervisor so
   they bind scoped inboxes (`_INBOX.hub`, `_INBOX.telegram-adapter`,
   `_INBOX.discord-adapter`) before the server ACLs narrow.
3. `sudo ./deploy/nats/gen-nkeys.sh --regen-authconf` — re-rendered
   `/etc/nats/nkeys/auth.conf` from the updated `acl-matrix.json`.
   Backup: `/etc/nats/nkeys/auth.conf.bak.20260422-161034`.
4. `sudo systemctl reload nats` — reload succeeded (`ExecReload=/bin/kill
   -HUP $MAINPID (status=0/SUCCESS)`); `nats.service` remained
   `active (running)` across the reload.
5. Ran three nkey-auth probes with the `hub` seed against
   `tls://127.0.0.1:4222`.

## Probe transcript

```
$ nats --nkey ~/.lyra/nkeys/hub.seed --tlsca <ca> \
       --server tls://127.0.0.1:4222 sub "_INBOX.hub.>"
Subscribing on _INBOX.hub.>
# (no permission violation — subscription stays open)

$ nats --nkey ~/.lyra/nkeys/hub.seed --tlsca <ca> \
       --server tls://127.0.0.1:4222 sub "_INBOX.>"
Subscribing on _INBOX.>
nats: error: nats: permissions violation: Permissions Violation for
Subscription to "_INBOX.>"

$ nats --nkey ~/.lyra/nkeys/hub.seed --tlsca <ca> \
       --server tls://127.0.0.1:4222 sub "_INBOX.telegram-adapter.>"
Subscribing on _INBOX.telegram-adapter.>
nats: error: nats: permissions violation: Permissions Violation for
Subscription to "_INBOX.telegram-adapter.>"
```

## Acceptance criteria closed

- **SC-9** — narrowed `auth.conf` regenerated and loaded without server
  restart. Reload exit status 0, service uninterrupted.
- **SC-10** — bus-wide `_INBOX.>` subscribe denied for `hub` (covers all
  lyra-owned identities since they share the same narrowing pattern).
- **SC-11** — cross-identity probe (`hub` → `_INBOX.telegram-adapter.>`)
  denied, confirming identities cannot eavesdrop on each other's inboxes.

Post-reload, supervisor reported `lyra_hub`, `lyra_telegram`,
`lyra_discord` all `RUNNING` with no restart loops; hub log showed no
`permissions violation` entries on its own subscriptions, confirming the
new code narrows inbox prefixes correctly.

## Satellite follow-up

`voice-tts`, `voice-stt`, `image-worker` rows in `acl-matrix.json` remain
on the bus-wide `_INBOX.>` grant per ADR-047 — each satellite owns its
own connect-site PR and will narrow when voiceCLI / imageCLI add
`inbox_prefix="_INBOX.<identity>"` to their `nats_connect` call. Those
PRs will append to this document.
