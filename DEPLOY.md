# Deploy — catena.borant.eu

Port **8022**, behind Caddy, on the borant VPS.

Host address, account and application paths are deliberately not written here:
this repository is public, and those live in the private infrastructure notes.
What follows uses `$APP` for the deployment directory.

Ask the machine which port is free rather than a document — a page is updated
when somebody remembers, a server is not:

```bash
ss -ltnp | grep 127.0.0.1
```

DNS needs nothing: `*.borant.eu` is a wildcard through Cloudflare, so a new
hostname resolves before Caddy has ever heard of it.

## First install

Note that `borant.eu` sits behind Cloudflare, so port 22 does not reach the
origin: connect to the host itself, whose address is in the private
infrastructure notes. `$APP` below is the deployment directory.

```bash
git clone https://github.com/that-ugly-cat/catena.git "$APP"
cd "$APP" && cp .env.example .env
printf 'JWT_SECRET=%s\n' "$(openssl rand -hex 32)" >> .env
docker compose up -d --build
```

First user:

```bash
docker exec -it catena python seed.py you@example.org "Your Name" "<password>"
```

Then configure the Zotero key and create the first MCP key from `/profile`.

## Caddy

The site block is generated from the paths declared in the code rather than
written by hand, so that the day a public route is added somebody notices while
writing it:

```bash
python caddy.py           # local mode
python caddy.py --gated   # behind Borant ID
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

**1. Caddy in front.** Generate the block with `python caddy.py --gated` and
paste it in. It uses the house snippets, and the `route{}` inside them is not
cosmetic: it guarantees erase → authenticate → strip cookie in that order.
`noforge` appears in the public branch too, because on a public path there is no
`forward_auth` to overwrite a forged header.

The matcher lists the **public** paths and not the private ones, so a route
added six months from now is born closed rather than open. Note what is public:
`/` is a showcase that never looks at its reader, and `/login` and `/logout` are
there because in gateway mode the app turns them away itself.

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
docker exec -it catena python link_borant.py you@example.org 01SUBJECT...
```

Linking is deliberately manual. `catena` looks a person up by `borant_sub` and
never by email, because an email is something a gate operator types: matching on
it would let a typo hand one person another person's Zotero key. If you forget,
nothing breaks and nobody is locked out — a second profile appears under
`<sub>@borant.invalid`, the log says why, and `link_borant.py` still fixes it.

### Rollback

`AUTH_MODE=local` in `.env` plus `docker compose up -d`, and drop
`import borantid` from the Caddy block. The first one on its own is enough.

**But only if a local account with a known password exists.** A profile the gate
provisioned carries a random password nobody has ever seen, and catena has no
password reset: falling back to `local` with only gate-provisioned accounts
leaves you outside your own app. So on an installation that will be gated, seed
a real account first and link it:

```bash
docker exec -it catena python seed.py you@example.org "You" '<password>'
docker exec -it catena python link_borant.py you@example.org 01YOURSUB
```

That is two doors instead of one, and the second is the one you need on the day
the gate is down. Skipping it is a defensible choice — one fewer credential to
keep — but then the rollback is `AUTH_MODE=local` *plus* a fresh `seed.py`, and
that is worth knowing before rather than during.

## The translation server

Ingest needs Zotero's own translators — the ones behind the connector button —
because catena never builds an item from a title.

**It is built from source, not pulled, and that is not fastidiousness.** On
Docker Hub `zotero/translation-server:latest` and `:2.0.6` are published for
**arm64 only** (January 2025); the newest amd64 tag is `2.0.4`, from January
2021. Pull it on an amd64 host and the container starts, warns once about the
platform, and then never answers — a failure that reads like networking and is
not. Building also fetches current translators, which is what resolving a URL
depends on.

```bash
cd "$APP" && ./vendor-translation.sh && docker compose up -d --build
```

`vendor-translation.sh` clones the source with its submodules — they are not
optional, the code loads `modules/translate` at runtime — and then fixes the
second thing that bites on this machine.

**Its `package.json` declares one dependency as a git URL**, and inside the
build container git has no credentials, so `npm install` goes out anonymously
and gets the same 401 that bites `git clone` here:

```
git ls-remote https://git@github.com/zotero/wicked-good-xpath.git
error: RPC failed; HTTP 401
```

The script rewrites that dependency to the codeload tarball of the same pinned
commit — plain HTTPS rather than git, so the throttle does not apply. Re-run it
to update the translators.

`vendor/` is git-ignored, so it survives `git pull` and is never committed.

Check it, from inside the app container, on the compose network:

```bash
docker compose exec app python -c "from catena.server.translation import Translation; print('alive:', Translation().alive())"
```

`alive: True` means the ladder in SPEC §5 has something to climb. If it is
False, ingest is the only thing that stops working — the audit, the injector,
the citation fields and the whole read surface never touch it. Look at
`docker compose logs translation` first; a platform warning there is the
symptom above.

Each rung, once it is up:

```bash
docker compose exec translation sh -c 'for id in 10.1016/j.socscimed.2011.05.031 30798313 9780199212094; do printf "%-34s " "$id"; curl -s -d "$id" -H "Content-Type: text/plain" localhost:1969/search | head -c 80; echo; done'
```

## Updates

```bash
cd "$APP" && git pull && docker compose up -d --build
```

**On this box, `git pull` needs a credential.** Anonymous git-over-HTTPS to
GitHub is throttled from that IP: it works for isolated requests and fails for
bursts, and the failure looks like an authentication problem — a 401 asking for
a username — which points at exactly the wrong thing. It cost half an hour on
2 September 2026 before the pattern was clear.

The fix is one fine-grained token, read-only, for the whole machine, so that
every deployed repository keeps its `https://` remote untouched: *Settings →
Developer settings → Personal access tokens → Fine-grained*, all repositories,
**`Contents: Read-only` and nothing else**, then stored with git's credential
helper. The exact command names an account, so it lives in the private
infrastructure notes.

It expires, silently, and on that day every deploy on the machine stops at once
with the same misleading 401. The expiry date belongs on a calendar.

Two smaller traps from the same evening. Do not chain git operations with `&&`
when the throttle is active — a burst is what trips it. And never pipe `git
fetch` into `head`: the early pipe close sends SIGPIPE, git dies mid-transfer,
and the next `merge --ff-only` reports "already up to date" while the working
tree stays on the old commit.

Migrations are additive and run at startup (`init_db()` in `models.py`): no
manual step, and no automatic rollback either. Columns are never renamed and
never dropped.

## Backup

The database is a single file on the mounted volume:

```bash
sqlite3 "$APP/data/catena.db" ".backup 'catena-$(date +%F).db'"
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
