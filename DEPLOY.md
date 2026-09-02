# Deploy — catena.borant.eu

Port **8022**, behind Caddy, on the borant VPS (`/opt/apps/catena`).

The port has to be free on the loopback of that box, and 8021 was already
taken. Before deploying anything here again, check: `ss -ltnp | grep 127.0.0.1`.

## First install

```bash
# borant.eu sits behind Cloudflare, so port 22 does not reach the origin:
# connect to the host itself.
ssh spit@178.105.139.118
sudo mkdir -p /opt/apps/catena && sudo chown spit:spit /opt/apps/catena
git clone https://github.com/that-ugly-cat/catena.git /opt/apps/catena
cd /opt/apps/catena
cp .env.example .env
printf 'JWT_SECRET=%s\n' "$(openssl rand -hex 32)" >> .env
docker compose up -d --build
```

First user:

```bash
docker exec -it catena python seed.py spit@example.org "Giovanni Spitale" "<password>"
```

Then configure the Zotero key and create the first MCP key from `/profile`.

## Caddy

```
catena.borant.eu {
    reverse_proxy localhost:8022
}
```

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

## Behind Borant ID

Optional, and off by default. `AUTH_MODE=local` is the default for security
before portability: an app that believes an identity header with nothing in
front of it hands its identity to anyone who can send that header.

Two things have to be true at once, and neither works without the other.

**1. Caddy in front.** Use the house snippets — the `route{}` block inside them
is not cosmetic, it guarantees erase → authenticate → strip cookie in that
order, and `noforge` has to be imported in the public branches too, because on a
public path there is no `forward_auth` to overwrite a forged header.

```
catena.borant.eu {
    @public path /healthz /static/*
    handle @public {
        import noforge
        import nocookie
        reverse_proxy localhost:8022
    }
    handle {
        import borantid
        reverse_proxy localhost:8022
    }
}
```

The matcher lists the **public** paths and not the private ones, so that a route
added six months from now is born closed rather than open. Note that `/login`
and `/logout` are not public here: in gateway mode this app has no sign-in of
its own, so there is nothing to leave open.

**2. The app told to trust it.** In `.env`:

```
AUTH_MODE=gateway
BORANT_TRUSTED_PROXY=172.17.0.1
```

`BORANT_TRUSTED_PROXY` is the address requests actually arrive from, and under
Docker that is the bridge gateway, **not** `127.0.0.1`. Read the real value off
the running container:

```bash
docker exec catena sh -c "ip route | awk '/default/ {print \$3}'"
```

Get it wrong and every request is silently anonymous: the symptom is that
everything looks fine and nobody ever gets in. The reason is in the log, once
per request.

### Before switching it on

Link the existing accounts to their Borant ID subjects, or the first person
through the gate lands in a brand-new empty profile while their bindings and
Zotero credential stay on the old row:

```bash
docker exec -it catena python link_borant.py                    # what is linked
docker exec -it catena python link_borant.py spit@example.org 01ABCDEF...
```

Linking is deliberately manual. `catena` looks a person up by `borant_sub` and
never by email, because an email is something a gate operator types: matching on
it would let a typo hand one person another person's Zotero key. If you forget,
nothing breaks and nobody is locked out — a second profile appears under
`<sub>@borant.invalid`, the log says why, and `link_borant.py` still fixes it.

### Rollback

`AUTH_MODE=local` in `.env` plus `docker compose up -d`, and drop
`import borantid` from the Caddy block. The first one on its own is enough —
which is why accounts provisioned through the gate still get a real (random)
password rather than none.

## The translation server

Ingest (SPEC §5) needs Zotero's container alongside. It is not wired into the
compose file yet because ingest does not exist. When it does:

```yaml
  translation:
    image: zotero/translation-server
    restart: unless-stopped
    expose: ["1969"]        # internal to the compose network, never published
    mem_limit: 400m
```

and `catena` reaches it at `http://translation:1969`.

## Updates

```bash
cd /opt/apps/catena && git pull && docker compose up -d --build
```

Migrations are additive and run at startup (`init_db()` in `models.py`): no
manual step, and no automatic rollback either. Columns are never renamed and
never dropped.

## Backup

The database is a single file on the mounted volume:

```bash
sqlite3 /opt/apps/catena/data/catena.db ".backup '/tmp/catena-$(date +%F).db'"
```

What is lost if it disappears: bindings can be retyped in two minutes,
`ingest_event` rows cannot — and they are the idempotency key of SPEC §9.1.
Without them a re-ingest repeats its writes to Zotero instead of recognising
them.

**The database holds users' Zotero keys in clear text.** Treat the backup as a
secret: do not leave it in `/tmp`, do not copy it anywhere you would not copy a
password.

## Monitoring

`/healthz` sits outside the gate, so it stays green even when the gate is down
and nobody can get in anywhere. A useful check also hits a gated route. The
endpoint reports which auth mode is live, which is the quickest way to tell
whether a rollback actually took effect.

## Local development

```bash
cp .env.example .env
printf 'JWT_SECRET=%s\n' "$(openssl rand -hex 32)" >> .env
uv run --env-file .env --extra server python seed.py you@example.org "You" pw
uv run --env-file .env --extra server uvicorn catena.server.main:app --reload --port 8022
```
