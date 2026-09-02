# catena

Riferimenti Zotero dentro documenti Word, con la ragione della citazione attaccata.

Il nome viene dalla *catena* medievale: una compilazione di passi citati da autorità diverse, incatenati al testo che commentano. Vale anche in senso letterale, per la catena di identificatori che va da una ricerca bibliografica al campo dentro il `.docx` senza che nessuno trascriva niente a mano.

`catena` **non** è un gestore di bibliografia (quello è Zotero), non è un tracker di progetti (PaperTrail), non è un motore di ricerca della letteratura (Contrarian). È il tessuto connettivo fra i tre. E non scrive prosa: inserisce, aggiorna e rimuove riferimenti, e basta.

La specifica completa è in [SPEC.md](SPEC.md), chiusa alla versione 1.0. Ogni affermazione tecnica che contiene è verificata su materiale reale, e dice dove.

## Stato

Primo pezzo: l'**audit**, in sola lettura. Nessuna rete, nessuna credenziale, nessuna dipendenza — tutto quello che controlla è deducibile dal file.

```bash
python -m catena.cli audit manoscritto.docx
```

Segnala:

- **surrogati duplicati** — lo stesso paper citato sotto due URI diversi, che produce due voci di bibliografia e due numeri. In APA diventa una disambiguazione per anno (`2008a`/`2008b`) che non esiste. È il difetto più insidioso perché il documento sembra corretto;
- **URI legati a un profilo Zotero locale**, che risolvono su una sola macchina al mondo e per i coautori sono orfani;
- **item senza URI o senza dati incorporati**, cioè citazioni che si romperanno in mano a qualcun altro;
- **metadati sporchi** — DOI che non sono DOI, titoli mancanti;
- `citationID` duplicati, `fieldType` inatteso, bibliografia mancante, stili con note a piè di pagina (non ancora gestiti), revisioni tracciate da conservare.

Codice d'uscita: `0` pulito, `1` almeno un errore (o un avviso con `--strict`), `2` file illeggibile.

## Perché l'audit per primo

Vale già da solo su manoscritti che esistono, scritti anche da altri e anni fa; non richiede che il resto di `catena` sia pronto; e serve a validare `catena` stessa quando lo sarà. Sul fixture dello spike ritrova **staticamente** il difetto che prima si poteva osservare solo aprendo Word.

## Sviluppo

```bash
uv run --with pytest python -m pytest tests -q
```

`spike/` contiene il generatore del fixture: un `.docx` con campi Zotero costruito a mano in OOXML, aperto in Word, sottoposto a Refresh e a cambio di stile. È il banco di prova di tutto il resto — vedi [spike/README.md](spike/README.md).

## Cosa manca

Il server MCP, l'ingest via translation-server, l'iniettore. I flussi previsti sono descritti in SPEC §13; due casi restano senza esemplare reale e il codice li rifiuta esplicitamente invece di indovinare: i documenti misti Zotero + manuale (§13.5) e gli stili con note a piè di pagina (§7.6).
