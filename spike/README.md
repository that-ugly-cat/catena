# spike §7 — il round-trip in Word

Quello che serve a `catena` e che nessuno script può verificare da solo: se un
`.docx` con campi Zotero **generati da noi** si comporta come uno generato da
Zotero.

## Rigenerare

```bash
uv run build_fixture.py fixture_items.json catena-spike.docx
uv run check_fixture.py catena-spike.docx     # 33 controlli statici
```

`fixture_items.json` contiene tre item reali di `groups/6378365` (ETHOS review)
con il loro CSL-JSON. Per usarne altri, basta rigenerarlo dall'API con
`include=csljson,data`.

## La prova, in Word

Aprire `catena-spike.docx` con Zotero attivo, premere **Refresh**, e leggere i
cinque paragrafi.

| # | Caso | Atteso |
|---|---|---|
| 1 | `uris` corretti, `id` stringa | `(1)` |
| 2 | `uris` corretti, **`id` assente** | `(2)`, nessun prompt |
| 3 | ripetizione dell'item 1 | di nuovo `(1)` |
| 4 | due item in un campo solo | `(2,3)` raggruppati |
| 5 | `uris` verso una key inesistente | si formatta dai dati incorporati |

Poi **Document Preferences → APA** e di nuovo Refresh: tutti e cinque devono
riformattarsi in autore-data, e la bibliografia in fondo deve seguire.

Prima del Refresh le citazioni mostrano `[1]`, `[2]`… scritti a mano: i
`formattedCitation` sono deliberatamente vuoti, così si vede quanto sarebbe
brutto un documento senza pre-render (SPEC §11.1, punto 9).

## Esito, 2 settembre 2026

| # | Vancouver | APA | |
|---|---|---|---|
| 1 | `(1)` | `(Assan et al., 2019)` | ✓ |
| 2 | `(2)`, nessun prompt | `(Bonham et al., 2009)` | ✓ |
| 3 | `(1)` | `(Assan et al., 2019)` | ✓ |
| 4 | `(2,3)` | `(Bonham et al., 2009; Rosato et al., 2008a)` | ✓ |
| 5 | `(4)` + voce doppia | `(Rosato et al., 2008b)` | ✗ |

Il cambio di stile da Word funziona su un documento che Zotero non ha mai
scritto: è la prova del requisito originale.

Il caso 5 fallisce come descritto sotto. In APA il difetto è più visibile — la
disambiguazione marca lo stesso paper `2008a` e `2008b`.

## Cosa significano gli esiti

Il caso che conta di più è il **2**. Il sorgente di Zotero dice che con `uris`
presenti il campo `id` non viene mai letto (SPEC §7.7), quindi ometterlo deve
essere innocuo. Se invece Word apre un dialogo di riselezione, la §7.7 è
sbagliata e va riscritta.

Il **5** decide se lo staging della §3.2 è praticabile: se un URI che non
risolve produce un prompt invece di ricadere sui dati incorporati, depositare
in un gruppo che i coautori non vedono diventa inutilizzabile.

Il **4** decide se le citazioni multiple sono un dettaglio di sintassi o un
problema vero (SPEC §11.2, punto 12).
