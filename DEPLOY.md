# Deploy — catena.borant.eu

Porta **8021**, dietro Caddy, sul VPS borant (`/opt/apps/catena`).

## Prima installazione

```bash
ssh spit@borant.eu
sudo mkdir -p /opt/apps/catena && sudo chown spit:spit /opt/apps/catena
git clone https://github.com/that-ugly-cat/catena.git /opt/apps/catena
cd /opt/apps/catena
cp .env.example .env
printf 'JWT_SECRET=%s\n' "$(openssl rand -hex 32)" >> .env
docker compose up -d --build
```

Primo utente:

```bash
docker exec -it catena python seed.py spit@example.org "Giovanni Spitale" "<password>"
```

Poi da `/profile` si configura la chiave Zotero e si crea la prima chiave MCP.

## Caddy

In `/etc/caddy/Caddyfile`:

```
catena.borant.eu {
    reverse_proxy localhost:8021
}
```

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

## La translation-server

L'ingest (SPEC §5) ha bisogno del container di Zotero accanto. Non e' ancora
cablato nel compose perche' l'ingest non esiste: quando servira',

```yaml
  translation:
    image: zotero/translation-server
    restart: unless-stopped
    expose: ["1969"]        # solo sulla rete interna del compose, mai pubblicata
    mem_limit: 400m
```

e `catena` la raggiunge su `http://translation:1969`.

## Aggiornamenti

```bash
cd /opt/apps/catena && git pull && docker compose up -d --build
```

Le migrazioni sono additive e girano da sole all'avvio (`init_db()` in
`models.py`): nessun passo manuale, nessun rollback automatico, e le colonne non
si rinominano ne' si droppano.

## Backup

Il database e' un file solo, nel volume montato:

```bash
sqlite3 /opt/apps/catena/data/catena.db ".backup '/tmp/catena-$(date +%F).db'"
```

Cosa si perde se sparisce: i binding si riscrivono a mano in due minuti, gli
`ingest_event` no — e sono la chiave di idempotenza della SPEC §9.1. Senza
quelli, un reingest ripete le scritture su Zotero invece di riconoscerle.

**Nel database ci sono le chiavi Zotero degli utenti, in chiaro.** Il backup va
trattato come un segreto: non lasciarlo in `/tmp`, non copiarlo dove non
copieresti una password.

## Dev locale

```bash
cp .env.example .env
printf 'JWT_SECRET=%s\n' "$(openssl rand -hex 32)" >> .env
uv run --env-file .env --extra server python seed.py you@example.org "Tu" pw
uv run --env-file .env --extra server uvicorn catena.server.main:app --reload --port 8021
```
