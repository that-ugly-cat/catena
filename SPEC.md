# catena — SPEC

*Riferimenti Zotero dentro documenti Word, con la ragione della citazione attaccata.*
*Versione 1.0 — 2 settembre 2026 — **chiusa**.*

*Da qui in avanti la specifica non guida più il lavoro: lo registra. Le decisioni nuove nascono dal codice e dai test sul materiale vero, e tornano qui solo quando sono state prese davvero — con la data e la ragione, come tutto il resto di questo documento. Una specifica che continua a crescere prima dell'implementazione sta descrivendo un programma che nessuno ha ancora provato a scrivere.*

*Cosa è verificato, e come: formato del campo Word letto da un manoscritto reale (§7), generato e ricontrollato staticamente (§12.1), provato in Word con Refresh (§12.2) e con cambio di stile Vancouver→APA (§12.3); permessi della chiave Zotero (§2.2); comportamento di `Zotero-Write-Token` letto nel sorgente di pyzotero (§9.1); risoluzione degli URI letta nel sorgente di Zotero (§7.7); forma dei record Contrarian (§6.1); riconoscitore delle citazioni tarato su un draft reale (§14).*

*Cosa resta senza esemplare: il caso misto Zotero + manuale (§13.5) e gli stili con note a piè di pagina (§7.6). Entrambi rifiutati esplicitamente dal codice finché non ci sarà un file su cui tararsi.*

---

## 0. Cos'è, e cosa non è

`catena` lega tre cose che oggi sono separate: una collezione Zotero, un manoscritto Word, e — quando la reference viene da una verifica — il passaggio verbatim che giustifica la citazione.

Il nome viene dalla *catena* medievale: una compilazione di passi citati da autorità diverse, incatenati al testo che commentano. Vale anche in senso letterale, per la catena di identificatori che va da una ricerca bibliografica al campo dentro il `.docx` senza che nessuno trascriva niente a mano.

**Non è** un gestore di bibliografia (quello è Zotero), non è un tracker di progetti (quello è PaperTrail), non è un motore di ricerca della letteratura (quello è Contrarian). È il tessuto connettivo fra i tre.

**E non scrive prosa.** Deciso il 2 settembre 2026, ed è un confine duro, non una funzione rimandata: `catena` inserisce, aggiorna e rimuove **riferimenti**. Il testo di un manoscritto lo scrive una persona, eventualmente aiutata da Ono in conversazione, e arriva nel file per le mani di chi lo firma. Un tool che sa mettere le mani sia nella bibliografia sia nel testo è un tool di cui bisogna fidarsi due volte; questo chiede di fidarsi una volta sola, e su una cosa verificabile.

**Invariante di progetto:** un paper = una collezione.

---

## 1. Architettura

Due pezzi, e la divisione non è negoziabile.

### 1.1 Il server (`catena.borant.eu`)

Superficie MCP più app web minima per la configurazione. Fa tutto ciò che riguarda *riferimenti*: parla con l'API web di Zotero, con la translation-server, tiene i legami paper↔collezione, e — questo è il punto — **restituisce il campo Word già confezionato** come stringa.

### 1.2 L'iniettore locale (`catena-inject`)

CLI che gira sulla macchina dove sta il file. Apre l'OOXML, trova i marker, splicia dentro le stringhe che il server ha prodotto, richiude. **Non ha logica bibliografica, non ha chiavi API, non sa cos'è un DOI.**

### 1.3 Perché il `.docx` non passa dall'MCP

Un MCP scambia JSON dentro la conversazione: un manoscritto da due megabyte non ci entra, e comunque il file sta sul disco dell'utente mentre il server sta sul VPS — un path non risolve. Caricarlo e riscaricarlo si potrebbe, ma significa storage, ciclo di vita dei file e due round-trip per spostare byte che sono già dove servono.

Conseguenza voluta: la parte intelligente sta in un posto solo, versionata, raggiungibile anche da una sessione dove il repo non c'è; la parte che ha bisogno del file è l'unica che deve stare dove sta il file.

---

## 2. Identità, chiavi, perimetro delle scritture

Ogni utente configura la **propria** API key Zotero nella web UI. Il server non ha mai una chiave "di sistema": ogni chiamata MCP corre coi permessi del suo proprietario, come in PaperTrail. Un gruppo che la chiave non vede risponde "non trovato", non "vietato".

### 2.1 Perimetro

Le scritture di `catena` sono confinate a **una sola group library dedicata**, per default `Ono - Catena` (6656239). Le collezioni nuove nascono lì. Le librerie di lavoro reali — quella personale e i gruppi dei paper — sono **sola lettura**.

Non esiste `delete_item`, non esiste `delete_collection`, non esiste `update_item` su item preesistenti. L'errore peggiore possibile è "una collezione di troppo in un gruppo dedicato", che si cancella a mano in tre secondi.

### 2.2 Forma corretta della chiave

Verificato il 2 settembre 2026: una chiave con `access.groups.all.write = true` concede scrittura a **ogni gruppo futuro**, perché gli override per-gruppo valgono solo per quelli già esistenti. La configurazione corretta nega per default:

```
Personal Library : library access, NO write, NO files
All Groups       : Read Only
Ono - Catena     : Read/Write   (unica eccezione, esplicita)
```

La web UI di `catena` legge `GET /keys/current` al momento della configurazione e **rifiuta una chiave con `groups.all.write = true` o con write sulla libreria personale**, spiegando perché. Un perimetro che si allarga da solo non è un perimetro.

La chiave di sviluppo è stata riconfigurata in questa forma il 2 settembre 2026 e serve da riferimento per il validatore:

```
user            : { library: true, files: true }          -- niente write
groups.all      : { library: true, write: false }         -- il default nega
groups.6656239  : { library: true, write: true }          -- unica eccezione
i restanti 10   : write: false, espliciti
```

`files: true` senza `write` è lettura degli allegati: innocuo, e potenzialmente utile se un giorno servisse il fulltext dei PDF già in libreria.

---

## 3. Modello dati (server)

```
binding
  id
  user_id
  label                    -- "against AB conflict"
  source_library           -- "groups/6378365"  (dove vivono le ref del paper)
  source_collection_key    -- nullable: tutta la libreria se assente
  deposit_library          -- "groups/6656239"  (dove si scrive; sempre scrivibile)
  deposit_collection_key
  papertrail_project_id    -- nullable
  csl_style                -- "http://www.zotero.org/styles/vancouver"
  locale                   -- "en-GB"
  created_at, updated_at

ingest_event               -- audit e chiave di idempotenza
  id, binding_id
  identifier, identifier_kind          -- UNIQUE (binding_id, identifier)
  item_key, item_library
  source ('manual' | 'contrarian'), run_id, verdict
  promoted_to_key          -- nullable, vedi §3.2
  created_at
```

Il `papertrail_project_id` è la chiave che chiude il cerchio: se il binding conosce il progetto, «le reference di quel paper» si risolve senza che l'utente dica quale collezione.

### 3.1 Le due gambe

Un binding legge da una parte e scrive dall'altra. È il caso normale, non l'eccezione: le reference di un paper vivono nel gruppo di quel paper, su cui la chiave è deliberatamente in sola lettura (§2.2), mentre le aggiunte nuove atterrano nella libreria di deposito.

- `collection_items(binding)` **unisce le due gambe** e segnala i doppioni fra l'una e l'altra: lo stesso paper può esistere come due item distinti con due key diverse, ed è normale.
- `add_item` scrive **solo** sulla gamba di deposito. Se qualcuno prova a depositare sulla gamba di lettura, il tool rifiuta con un messaggio che nomina la libreria, non con un 403 crudo.
- `citation_field` preferisce sempre la key della gamba di **lettura** quando l'item esiste in entrambe: è quella che risolve per i coautori.

### 3.2 Il costo della gamba di deposito, e `reconcile`

Un item depositato in `Ono - Catena` ha URI `http://zotero.org/groups/6656239/items/<key>`. Quel gruppo è privato e i coautori non ne fanno parte: **per loro quell'URI non risolve**. La citazione continua a formattarsi — l'`itemData` è incorporato nel campo e Zotero ci ricade sopra — ma non è riagganciabile alla loro libreria.

**E c'è di peggio, verificato in Word il 2 settembre 2026 (§12.2, caso 5).** Quando un URI non risolve, Zotero non si limita a formattare dai dati incorporati: costruisce un *item surrogato*, registrato in `embeddedItemsByURI` sotto quell'URI e con un id di sessione. Il surrogato è un'entità distinta dall'item vero. Nel fixture, la citazione con URI rotto verso Rosato ha ricevuto **il numero (4) e una quarta voce di bibliografia identica alla terza** — lo stesso paper, contato due volte.

L'unificazione avviene per URI, non per contenuto: due citazioni che puntano allo stesso URI rotto condividono un surrogato, ma un paper raggiungibile per due URI diversi — uno che risolve e uno no — produce due voci e due numeri. Ed è esattamente lo stato misto del ciclo di promozione qui sotto: alcuni item già spostati nel gruppo vero, altri ancora in staging.

Negli stili autore-data il danno è più vistoso: la disambiguazione di CSL interpreta i due surrogati come due lavori distinti dello stesso autore nello stesso anno e li marca `2008a` e `2008b` (§12.3). Un revisore lo nota; un lettore distratto pensa che siano due paper.

Due conseguenze operative, entrambe vincolanti:

- la regola della §3.1 — `citation_field` preferisce la key della gamba di lettura quando l'item esiste in entrambe — **non è una preferenza ma un obbligo**: violarla duplica la bibliografia;
- `reconcile` **va eseguito prima che il documento esca di mano**, non «quando capita». Un manoscritto mandato ai coautori con URI di staging non è solo meno riagganciabile: ha una bibliografia sbagliata.

Quindi `Ono - Catena` è **un'area di staging, non una destinazione**. Il ciclo previsto:

1. `catena` deposita in staging e registra l'`ingest_event`;
2. l'utente, che sul gruppo vero ha i permessi che la chiave non ha, sposta gli item nella collezione reale da Zotero — trascinandoli, come farebbe comunque;
3. `reconcile(binding)` ricerca ogni item di staging nella gamba di lettura (DOI, poi ISBN, poi titolo), scrive `promoted_to_key`, e da lì in avanti `citation_field` emette l'URI buono.

Per i campi già iniettati in un `.docx`, `catena-inject --reconcile` riscrive gli URI in loco. Finché non gira, quelle citazioni funzionano lo stesso: perdono solo il riaggancio.

L'alternativa è allargare la chiave in scrittura sul gruppo del paper mentre ci si lavora, e allora la gamba di deposito coincide con quella di lettura e tutto questo paragrafo non si applica. È una scelta per binding, non di sistema.

---

## 4. Superficie MCP

### 4.1 Letture (libere)

```
list_bindings()
get_binding(label)
list_collections(library)
collection_items(binding)          -> CSL-JSON + item key + URI
search_library(query, library?)    -> lessicale, su tutte le librerie leggibili
resolve_identifier(identifier)     -> metadati via translation-server, NIENTE scrittura
citation_field(binding, keys[], locator?, prefix?, suffix?, suppress_author?)
                                   -> stringa ADDIN ZOTERO_ITEM pronta
bibliography_field(binding)        -> stringa ADDIN ZOTERO_BIBL
document_prefs(binding)            -> le due stringhe ZOTERO_PREF_1 / _2
```

### 4.2 Scritture (conferma esplicita dell'utente, sempre)

```
create_binding(label, source_library, source_collection?, deposit_collection_name,
               deposit_library?, papertrail_project_id?)
create_collection(name)            -- solo nella library di deposito
add_item(binding, identifier)
add_verified(binding, identifier, run_id, key, paper_verdict, passages[])
add_to_collection(binding, item_key)
reconcile(binding)                 -- §3.2: riaggancia lo staging alla gamba di lettura
```

Errori restituiti come `{"error": ...}`, mai sollevati: un tool che lancia dà al modello uno stack trace su cui allucinare, un messaggio leggibile gli permette di correggersi.

### 4.3 Ingest in blocco: piano, poi applica

Una conferma per item non regge oltre la manciata: un draft con quaranta citazioni produrrebbe quaranta domande, e alla dodicesima nessuno legge più. Le aggiunte multiple passano quindi da due chiamate:

```
plan_ingest(binding, identifiers[])   -- lettura pura, nessuna scrittura
apply_ingest(plan_id)                 -- una conferma sola, per tutto il piano
```

`plan_ingest` risolve ogni identificatore e restituisce una riga per ciascuno con l'esito previsto: **nuovo** (con tipo di item e metadati risolti, §5.2), **già presente** (con la key e su quale gamba, §3.1), **ambiguo** (candidati da dirimere, §5.1), **irrisolvibile** (§5, caso 5). Il piano è un oggetto persistito, non un consiglio: `apply_ingest` esegue esattamente quello che il piano dichiarava, e fallisce se la libreria è cambiata nel frattempo.

Una conferma sola, ma su un piano leggibile per intero. È il contrario di quaranta conferme cieche.

---

## 5. Ingest: la scala degli identificatori

Verificato sulla libreria reale il 2 settembre 2026: il DOI copre il 93% degli articoli e quasi niente del resto. Su circa 16'000 item citabili, **circa uno su cinque non ha DOI** — 514 libri (97 con DOI, 434 con ISBN), 288 webpage (6 con DOI, 288 con URL), 131 report (10 con DOI, 126 con URL), 140 document (1 con DOI). La colonna senza buchi è l'URL.

`add_item` prova in quest'ordine, fermandosi al primo che risolve:

1. **DOI** → translation-server `/search`
2. **PMID / arXiv ID** → translation-server `/search`
3. **ISBN** → translation-server `/search`
4. **URL** → translation-server `/web` (gli stessi translator del connector)
5. **niente** → si ferma

Endpoint verificati sulla documentazione della translation-server (2 settembre 2026): `POST /search` accetta **DOI, ISBN, PMID e arXiv ID** come `text/plain` e restituisce Zotero API JSON; `POST /web` accetta un URL come `text/plain` e restituisce lo stesso formato, oppure `300 Multiple Choices` con una sessione da cui scegliere quando i risultati sono più d'uno. Ci sono anche `/export` e `/import`, che a `catena` non servono. Porta di default 1969. **Non provata end-to-end su questa macchina: docker non è installato.** Va fatto sul VPS, dove il container gira comunque.

Il caso 5 non è un fallimento del tool: è il confine. Se nessun identificatore risolve, `catena` **non compila l'item a mano**. Restituisce una bozza dei campi che l'utente conferma uno a uno, oppure lo invita ad aggiungerlo col connector e poi ritrovarlo con `search_library`. Metadati inventati da un modello hanno esattamente lo stesso aspetto di metadati veri: è l'unico errore di questo sistema che non si vede.

### 5.0 L'autore come controllo, non come identificatore

Quando l'identificatore arriva accompagnato da un nome — nei draft veri le citazioni provvisorie hanno la forma `[Assan, 10.1136/…]` — quel cognome non serve a risolvere niente, ma serve a **verificare**. Se il DOI risolve a un paper il cui primo autore non è Assan, il DOI è stato copiato male, e senza il controllo la citazione entrerebbe sbagliata con l'aria di essere giusta.

`plan_ingest` accetta quindi un `hint` testuale per identificatore e segnala la discordanza nel piano, senza bloccare: decide chi legge. È il presidio più economico contro l'errore più insidioso di tutto il flusso.

### 5.1 Dedup

Stessa logica a scala: DOI esatto → ISBN esatto → titolo normalizzato più anno più primo autore. **Sul ramo fuzzy il tool non decide**: mostra i candidati e chiede. Con 33'297 record in libreria e collezioni che si chiamano `Duplicates removed`, il costo di una domanda è minore del costo di un doppione.

### 5.2 Tipo di item

Il CSL formatta in base al tipo: un rapporto entrato come `webpage` esce sbagliato in bibliografia con qualunque stile. La translation-server oscilla fra `document`, `report` e `webpage` sulla letteratura grigia — i 140 `document` con un solo DOI in libreria sono il cassetto dove finisce ciò che non è stato classificato. Quindi `add_item` **riporta sempre il tipo scelto**, e su tutto ciò che non è `journalArticle` lo mette in evidenza perché l'utente possa smentirlo prima che entri.

---

## 6. Provenienza: cosa arriva da Contrarian

`add_verified` è il tool che giustifica l'esistenza di `catena`.

Contrarian produce, per ogni paper letto: `paper_verdict` (`supports` | `contradicts` | `mixed` | `irrelevant`) e `passages` — citazione **verbatim**, sezione, `bearing`, e una riga di perché. Oggi quella roba vive solo nella traccia. `add_verified` la porta dentro Zotero:

- **tag** che porta il bearing, non solo la provenienza: `catena:supports`, `catena:contradicts`, `catena:qualifies`;
- **nota figlia** dell'item con le passages verbatim, la sezione, il perché e l'URL della traccia del run;
- riga in `ingest_event` con il `run_id`.

Il risultato: fra otto mesi, aperto il manoscritto, la ragione per cui quel paper è citato lì sta dentro Zotero, attaccata all'item, a due click dalla citazione in Word — e viaggia coi coautori se la collezione è in un gruppo.

**Vincolo che deriva dal modello dati di Contrarian, non da una policy inventata:** `log_verification` definisce `irrelevant` come «nessun passaggio citabile che riguardi il claim». Quindi **un item con verdetto `irrelevant` non diventa mai una citazione**, e `citation_field` si rifiuta di produrne il campo.

Un run che chiude `unsupported` o `no_evidence` non produce citazioni ma produce un risultato: quella frase del manoscritto va ammorbidita o tolta. Va all'utente come report, non in collezione.

### 6.1 Forma di un record Contrarian, e cosa ne consegue

Verificato con un run di prova il 2 settembre 2026 (`hasstZLyOQuT`, pubmed e openalex):

```json
{ "title": …, "abstract": …, "abstract_truncated": true,
  "authors": "Germani, Federico; Spitale, Giovanni; …",   // stringa, non lista
  "year": 2024, "doi": "10.2196/56307",
  "url": "https://doi.org/10.2196/56307",
  "source": "JMIR infodemiology", "database": "pubmed",
  "key": "10.2196/56307" }                                 // la key È il DOI
```

Due conseguenze.

**La prima è buona:** la `key` è il DOI, quindi il passaggio Contrarian→`catena` non ha bisogno di traduzione. `[R:10.2196/56307]` entra dritto in `add_item` e nella scala §5 al gradino 1. Non serve un ponte fra i due sistemi: il ponte è il DOI.

**La seconda è un limite netto:** Contrarian è DOI-centrico da capo a fondo — `key` è il DOI, `get_fulltext(doi)`, `snowball(doi)`. Quindi **il ramo Contrarian e il ramo letteratura grigia sono disgiunti**. Tutto ciò che non ha DOI — i 514 libri, i 288 webpage, i 131 report della §5 — può entrare in `catena` solo dalla porta manuale (ISBN, URL, connector), mai da una verifica. Un libro non si verifica con Contrarian e non si snowballa: se il manoscritto poggia su una monografia, quella resta una citazione senza `passages` e senza traccia. Non è aggiustabile qui, e va detto invece che scoperto a metà di un paper.

---

## 7. Il campo Word — formato verificato

Estratto da un manoscritto reale in lavorazione — 172 campi, Zotero 7.0.29, stile Vancouver. Non ricostruito dalla documentazione: letto dal file. (Il documento non è nel repository: è lavoro non pubblicato, e qui restano solo i fatti che se ne ricavano.)

### 7.1 Citazione

Campo Word (`fldChar begin` / `instrText` / `separate` / testo risultato / `end`) con:

```
ADDIN ZOTERO_ITEM CSL_CITATION {"citationID":"<8 char>","properties":{
  "formattedCitation":"(1)","plainCitation":"(1)","noteIndex":0},
  "citationItems":[{"id":<n>,"uris":["<URI>"],"itemData":{ ...CSL-JSON completo... }}],
  "schema":"https://github.com/citation-style-language/schema/raw/master/csl-citation.json"}
```

`instrText` va spezzato su più run per i campi lunghi, come fa Zotero.

Dall'analisi dei 172 campi del manoscritto (2 settembre 2026):

- **`citationID` è unico per occorrenza, non per item.** 172 valori distinti su 172 campi, anche dove lo stesso paper è citato sei volte. `catena` ne genera uno nuovo a ogni inserimento, 8 caratteri, e non lo riusa mai.
- **`noteIndex` è 0 ovunque** in quel documento, perché lo stile è in-text. Vedi §7.6.
- **`id` è un intero fra 46 e 1286: sono gli itemID *locali* di Zotero**, il contatore interno della sqlite di chi ha scritto il documento. L'API web non li restituisce e non li conosce: espone key alfanumeriche. Vedi §7.7 — è il buco aperto più serio della specifica.

### 7.2 URI — il giunto vero

```
http://zotero.org/groups/<groupID>/items/<itemKey>
http://zotero.org/users/<userID>/items/<itemKey>
```

Il giunto interno del sistema è la **item key di Zotero**, non il DOI: identica per un articolo, un libro o un rapporto OMS. Il DOI è solo una delle maniglie d'ingresso.

**Mai** generare `users/local/<localUserKey>/items/...`. Il manoscritto esaminato usa `users/local/etkLASSq`, chiave di un profilo che non esiste più su questa macchina: quelle citazioni si riagganciano su una macchina sola al mondo, e per tutti gli altri sono item orfani. È il motivo per cui `catena` passa dal cloud e non dalla sqlite locale.

### 7.3 Bibliografia

```
ADDIN ZOTERO_BIBL {"uncited":[],"omitted":[],"custom":[]} CSL_BIBLIOGRAPHY
```

### 7.4 Preferenze di documento

**Non sono un campo.** Stanno in `docProps/custom.xml` come proprietà custom `ZOTERO_PREF_1` e `ZOTERO_PREF_2`, spezzate perché Word tronca `vt:lpwstr` a 255 caratteri:

```xml
<data data-version="3" zotero-version="7.0.29">
  <session id="XXXXXXXX"/>
  <style id="http://www.zotero.org/styles/vancouver" locale="en-GB"
         hasBibliography="1" bibliographyStyleHasBeenSet="1"/>
  <prefs><pref name="fieldType" value="Field"/>
         <pref name="dontAskDelayCitationUpdates" value="true"/></prefs>
</data>
```

È il pezzo che rende vero il requisito originale: **una volta che queste proprietà ci sono, il cambio di stile non lo fa `catena`**. Lo fa Zotero, da Word, con Document Preferences.

**Dove cade il taglio dei 255.** Misurato sul manoscritto: `ZOTERO_PREF_1` è **255 caratteri non escapati**, che diventano 288 una volta serializzati in XML (`<` → `&lt;` e compagnia); `ZOTERO_PREF_2` è 50 non escapati, 65 escapati. Il limite di Word su `vt:lpwstr` è quindi sul **valore**, non sulla sua serializzazione: si spezza la stringa grezza a 255 e poi si escapa ciascun pezzo, mai il contrario. È un errore facile da fare — l'ha fatto la prima versione del controllo in `spike/check_fixture.py`, che misurava l'escapato e segnalava un falso positivo.

### 7.5 Testo visibile pre-renderizzato

`pyzotero` con `add_parameters(content='citation', style=...)` fa renderizzare al server Zotero il testo formattato. Torna HTML (`span` per le citazioni, `div` per la bibliografia) da spogliare; con `content='bib'` c'è un tetto di 150 item per chiamata.

**Ma funziona solo per metà degli stili, e non per quello di riferimento.** Il server rende ogni item *isolatamente*: non sa in che ordine compaiono nel documento, né quali citazioni siano raggruppate.

Misurato il 2 settembre 2026 su quattro item di una group library reale, stessa richiesta con due stili:

```
style=vancouver   ->  '<span>(1)</span>'   x4      ← tutti uguali
style=apa         ->  '<span>(Assan et al., 2019)</span>'
                      '<span>(Bonham et al., 2009)</span>'
                      '<span>(Rosato et al., 2008)</span>'
                      '<span>(Rajkhowa et al., 2025)</span>'
```

Non è un'inferenza: con uno stile numerico il server restituisce `(1)` per ogni item, perché ognuno è il primo della propria richiesta. E nel manoscritto reale i `formattedCitation` sono `(1)`, `(2)`, `(3)`, ma anche `(5,6)` e `(1,3,7)`: numeri d'ordine di prima apparizione, e raggruppamenti decisi dalla posizione. Nessuna chiamata per-item può produrli.

Vancouver, lo stile del manoscritto, è numerico. Quindi:

- **stili autore-data** → pre-render come descritto, il documento si apre impaginato;
- **stili numerici** → `catena` calcola da sé l'ordine di prima apparizione (l'iniettore conosce la sequenza dei marker, quindi l'informazione ce l'ha) **oppure** rinuncia al pre-render e scrive un segnaposto, lasciando che il primo Refresh in Word sistemi la numerazione.

La seconda opzione è più onesta e costa un click; la prima è più bella e va tenuta allineata alla logica di raggruppamento del CSL, che è esattamente il genere di riscrittura di citeproc che questa architettura voleva evitare. In entrambi i casi la bibliografia (§7.3) ha lo stesso problema, e va generata nell'ordine di citazione, non nell'ordine della collezione.

**Il round-trip toglie urgenza alla decisione.** Nel fixture i `formattedCitation` erano deliberatamente vuoti, e un solo Refresh ha prodotto numerazione corretta — `(1)`, `(2)`, `(1)` alla ripetizione, `(2,3)` sul raggruppamento — più la bibliografia completa. Quindi la via «segnaposto più un Refresh» **funziona end-to-end ed è dimostrata**, mentre il pre-render resta un abbellimento per gli stili autore-data, dove peraltro l'API già dà la risposta giusta. Raccomandazione: partire dal segnaposto, aggiungere il pre-render solo per gli stili autore-data e solo se serve.

### 7.6 Stili con note a piè di pagina

Nel manoscritto esaminato `noteIndex` è 0 in tutti i 172 campi: è un documento in-text. Con Chicago notes o MHRA — entrambi installati sulla macchina di riferimento — le citazioni vivono dentro le footnote, il campo va inserito in `word/footnotes.xml` e non in `document.xml`, e `noteIndex` deve numerare la nota.

Non c'è un esemplare verificato di quel formato. Finché non c'è, **`catena` supporta solo stili in-text e rifiuta esplicitamente un binding con uno stile note-based**, invece di produrre un documento che sembra funzionare e non funziona.

### 7.7 Il campo `id`: risolto leggendo il sorgente di Zotero

`citationItems[].id` è un intero locale (§7.1) che l'API web non espone. La domanda era se Zotero lo usi per risolvere l'item — nel qual caso un intero arbitrario potrebbe riagganciare la citazione al paper sbagliato nella libreria di chi apre il file.

**Non serve un esperimento: la risposta è nel sorgente.** `chrome/content/zotero/xpcom/integration.js`, metodo `loadItemData()`, la risoluzione è un `if/else`:

```js
if (citationItem.uris) {
    [zoteroItem, itemNeedsUpdate] = await …uriMap.getZoteroItemForURIs(citationItem.uris);
    …
} else {
    if (citationItem.key && citationItem.libraryID) …getByLibraryAndKey(…)
    else if (citationItem.itemID) …get(citationItem.itemID)
    else if (citationItem.id)     …get(citationItem.id)
}
```

Quattro conseguenze, tutte utili:

1. **Con `uris` presenti, `id` non viene mai consultato.** Il ramo che chiama `Zotero.Items.get(citationItem.id)` è raggiungibile solo quando `uris` manca del tutto. L'ipotesi del riaggancio sbagliato è esclusa, purché gli `uris` ci siano sempre.
2. **`id` è un output, non un input.** Subito dopo la risoluzione Zotero lo sovrascrive con l'id dell'item che ha trovato (`citationItem.id = zoteroItem.id`, o l'id di sessione per gli item incorporati). Qualunque valore `catena` ci metta viene rimpiazzato al primo Refresh.
3. **`uris` deve essere una lista non vuota, sempre.** Nel ramo di fallback su dati incorporati Zotero itera `citationItem.uris` per registrare l'item surrogato: con `uris` assente quel percorso si romperebbe. Vale anche per una citazione il cui item non è nella libreria del lettore.
4. **Il fallback su dati incorporati è confermato** (§3.2): se gli `uris` non risolvono ma `itemData` c'è, Zotero costruisce un item surrogato con un id di sessione e formatta normalmente. Senza `itemData` invece apre il dialogo di riselezione, o solleva `MissingItemException`.

C'è un unico punto in cui `id` conta davvero, ed è un percorso di recupero: quando la formattazione «All Caps» di Word per Mac corrompe il field code, Zotero **cancella deliberatamente `uris` e `itemData`** e ricade su `Zotero.Items.get(citationItem.id)`, perché i numeri sono l'unica cosa che l'uppercase non guasta. È un dettaglio da conoscere ma non cambia cosa scriviamo.

**Regola per `catena`:** `uris` sempre presente e corretto; `id` valorizzato per coerenza con `itemData.id`, sapendo che è decorativo.

**Confermato in Word il 2 settembre 2026** (§12.2, caso 2): una citazione senza alcun campo `id` si è risolta e formattata correttamente, senza dialogo di riselezione. Il sorgente diceva il vero. Per inciso, l'`id` che l'API restituisce dentro il CSL-JSON ha la forma `22892531/5BS3UJP2` — una stringa, non l'intero locale che compare nei documenti scritti da Zotero: un'ulteriore conferma che il campo non è una chiave.

---

## 8. L'iniettore locale

### 8.1 Marker

Nel manoscritto si scrive:

```
[R:<key>]        reference da un run Contrarian — la key È il DOI (§6.1)
[@Autore2024]    reference già in collezione — risolta autore-anno
[BIBLIOGRAPHY]   punto in cui va il campo bibliografia
```

Verificato il 2 settembre 2026 su un run reale: la `key` di un record Contrarian **è la stringa del DOI**, non un id opaco di sessione. Quindi `[R:10.2196/56307]` è un marker globalmente valido, non scoped al run — comodo, perché resta leggibile in una bozza anche fuori da `catena`, e perché coincide con l'identificatore che `add_item` userebbe comunque.

In ogni caso i marker vengono risolti al momento dell'inserimento e **non restano nel file**: nel `.docx` ci finisce il campo Zotero vero.

### 8.1.1 Scoperta: leggere i marker che non abbiamo scritto noi

I marker della §8.1 sono quelli che scriviamo noi. Un draft vero porta quello che ha digitato il suo autore: `[Assan, 10.1136/…]`, `[Bonham, https://…]`, `(Rosato 2008 — vedi link)`. Quindi l'iniettore ha un modo che non modifica niente:

```
catena-inject manoscritto.docx --discover
```

Legge il documento, riconosce i candidati con un insieme di pattern configurabile, ne estrae l'identificatore e **ne registra la posizione** — offset di paragrafo e di run — perché l'iniezione dovrà rimettere il campo esattamente lì. Restituisce l'elenco dei candidati con il testo grezzo di ciascuno, da dare in pasto a `plan_ingest` (§4.3).

Quello che non fa: indovinare. Un candidato senza identificatore riconoscibile finisce nell'elenco marcato come tale, non viene risolto per somiglianza del cognome. La regola della §8.3 vale anche qui — l'ambiguità si mostra, non si scioglie.

### 8.2 Modi

```
catena-inject manoscritto.docx --binding "against AB conflict"
catena-inject bozza.md --binding "..." --out manoscritto.docx
```

Il primo è il modo primario: il manoscritto esiste già in Word, con commenti e revisioni dei coautori, e va toccato il meno possibile. Il secondo passa da pandoc e poi dallo stesso iniettore.

### 8.3 Il lavoro vero

Word spezza il testo in `run` arbitrari: `[@Spitale2023]` può essere frammentato su cinque run e va ricomposto prima di essere sostituito. È lì che sta la difficoltà, non nella generazione dei campi. L'iniettore:

- **preserva** `w:ins`, `w:del`, ancoraggi dei commenti e footnote. Non «non li tocca»: la calibrazione (§14) ha trovato una citazione *dentro* un blocco `w:ins`, quindi il campo va inserito conservando la marcatura di revisione e la sua attribuzione. Un marker che attraversa il confine di un'ancora di commento va sostituito senza spezzare l'ancora, o non va sostituito affatto;
- rifiuta un file che contenga già campi `ZOTERO_ITEM` con `fieldType` diverso da `Field`;
- scrive sempre su una copia, mai in place;
- restituisce un report: marker risolti, non risolti, ambigui. **Un marker ambiguo blocca l'esecuzione**, non viene indovinato.

---

## 9. Modi di rompersi, e cosa li contiene

| # | Rischio | Contenimento |
|---|---|---|
| 1 | Doppioni su retry: POST riuscito, risposta persa, chiamante che ritenta | §9.1 — la protezione di pyzotero non copre il caso nostro; serve la chiave di idempotenza su `ingest_event` |
| 2 | La copia cloud è vecchia (ultimo sync utente: 19 agosto 2026) | Ogni lettura riporta `Last-Modified-Version` e il conteggio item, così l'anomalia si vede prima di generare, non dopo |
| 3 | Metadati inventati per item senza identificatore | §5: il tool si ferma, non compila |
| 4 | Tipo di item sbagliato sulla grigia, quindi bibliografia sbagliata | §5.2: tipo sempre riportato, evidenziato se non è `journalArticle` |
| 5 | Citazione formalmente perfetta di un paper che non dice quella cosa | §6: le passages verbatim finiscono nella nota dell'item. Non elimina il rischio, lo rende **falsificabile** da chiunque apra l'item |
| 6 | Perimetro che si allarga da solo | §2.2: la chiave viene validata in ingresso |
| 7 | Corruzione di un `.docx` con revisioni dei coautori | §8.3: mai in place, mai dentro le tracked changes |

Il rischio 5 è quello che conta. Tre superfici in catena, di cui due che scrivono, orchestrate da un modello: il modo in cui questa cosa si rompe non è generare un errore, è generare una bibliografia dall'aria impeccabile che nessuno ricontrolla.

### 9.1 `Zotero-Write-Token`: cos'è, e perché la protezione di pyzotero non basta

Il problema che il token risolve. Un POST che crea un item non è idempotente: se la richiesta arriva al server, l'item viene creato, e poi la risposta si perde per strada, il chiamante non può distinguere «non è arrivata» da «è arrivata e la conferma è andata persa». Se ritenta, ottiene due item identici con due key diverse. L'header `Zotero-Write-Token` — 32 caratteri esadecimali scelti dal client — dà al server un modo per riconoscere il duplicato: **Zotero ricorda i token visti nell'ultima ora e su un token già visto non ricrea niente**, restituisce l'esito della prima chiamata.

Il token è quindi utile solo se **è lo stesso fra il tentativo e il ritentativo**.

Verificato nel sorgente di pyzotero 1.15.1 (`src/pyzotero/_client.py`, `_utils.token()`):

```python
def token() -> str:
    """Return a unique 32-char write-token."""
    return str(uuid.uuid4().hex)
```

e lo manda in quattro punti — `create_items()`, `create_collections()`, `saved_search()`, `delete_saved_search()`. Gli altri metodi di scrittura non lo usano e non ne hanno bisogno: `addto_collection`, `update_item`, `update_items`, `moveto_collection` sono PATCH verso una URL nota, protette da `If-Unmodified-Since-Version`, e ripeterle è innocuo.

Quindi la doc era incompleta ma la libreria fa la cosa giusta — **per metà**. Il token è generato *dentro* il metodo, fresco a ogni invocazione:

- un retry **interno** a pyzotero (il backoff dei decoratori, `MAX_RETRY_ATTEMPTS = 3`) riusa lo stesso headers dict, quindi lo stesso token: **protetto**;
- una seconda chiamata a `create_items()` dal **chiamante** genera un token nuovo: **non protetto**.

E il nostro caso è il secondo. Il retry che temiamo non nasce nel client HTTP, nasce un livello sopra — un tool MCP che va in timeout e un modello che lo richiama. pyzotero non espone un parametro per passare il proprio token, quindi la protezione va costruita da `catena`, dove il retry accade:

**`ingest_event` porta `UNIQUE (binding_id, identifier)`.** Prima di ogni `create_items()` si inserisce la riga; se l'inserimento viola il vincolo, l'item esiste già e si restituisce l'`item_key` registrato invece di crearne un altro. È idempotenza a livello applicativo, che è il livello dove serve, e ha il vantaggio di non scadere dopo un'ora come la memoria dei token lato Zotero.

Resta scoperto solo il caso in cui il POST riesce e il processo muore prima di poter scrivere `item_key` nella riga già inserita. Si chiude alla prima riapertura: una riga di `ingest_event` con `item_key` nullo è un ingest da riconciliare, e la si risolve cercando l'identificatore nella libreria di deposito.

---

## 10. Stack

Stessa impalcatura di PaperTrail: FastAPI più Starlette, `mcp.server.mcpserver.MCPServer` montato di fianco all'app web, JWT in cookie httpOnly, SQLAlchemy, Docker e compose sul VPS.

Dipendenze specifiche:

- **pyzotero 1.15.1** (30 agosto 2026, Python ≥3.10, Blue Oak 1.0.0). Ci risparmia paging (`everything()`), chunking automatico a 50 item per richiesta, `If-Unmodified-Since-Version`, parsing `csljson`, `item_template()` e `check_items()` — che valida i **nomi dei campi** contro lo schema Zotero, non i valori: quelli restano un problema umano.
- **zotero/translation-server** in un container accanto: `/search` per gli identificatori, `/web` per gli URL.

### 10.1 Perché non il server MCP di pyzotero

`pyzotero[mcp]` esiste già ed espone la libreria come tool. Non sostituisce `catena` per una ragione strutturale — **parla solo con l'API locale**, richiede Zotero aperto sulla macchina dell'utente, quindi non può vivere sul VPS — e quattro pratiche: non inietta campi nel docx, non ha il legame paper↔collezione↔PaperTrail, non ha la scala DOI/ISBN/URL, non sa niente di Contrarian.

È però utile **installato read-only** come banco di prova sulla forma dei dati, prima che `catena` esista. Le scritture spente: il suo perimetro è più largo del nostro, e il flag `--enable-deletes` su una libreria da 33'297 record non deve stare a portata di chiamata.

---

## 11. Deciso, e cosa resta

Chiuse il 2 settembre 2026:

1. **Il modo primario è docx→docx.** Confermato. Il manoscritto esiste già in Word e va toccato il meno possibile: la §8.3 è lavoro vero, non un ramo opzionale.
2. **`Zotero-Write-Token`:** pyzotero lo manda su `create_items` e `create_collections`, ma con un uuid nuovo a ogni invocazione — copre i suoi retry interni, non quelli del chiamante. Idempotenza su `ingest_event`. Vedi §9.1.
3. **Record Contrarian:** la `key` è il DOI. Il ponte con `catena` non va costruito. Ma il ramo Contrarian e il ramo grigia sono disgiunti. Vedi §6.1.
4. **Binding a due gambe:** adottato. Lettura dal gruppo del paper, deposito in staging, `reconcile` per promuovere. Vedi §3.1 e §3.2.

Chiuse il 2 settembre 2026, secondo giro:

5. **Il campo `id` (§7.7).** Risolto leggendo `integration.js`: con `uris` presenti l'`id` non viene mai consultato, e Zotero lo sovrascrive comunque dopo la risoluzione. Nessun rischio di riaggancio sbagliato. Resta da confermare in Word che un `id` assente non dia noia — caso 2 del fixture.
6. **Il taglio dei 255 in `ZOTERO_PREF` (§7.4).** È sul valore non escapato, misurato.
7. **Endpoint della translation-server (§5).** `/search` per DOI, ISBN, PMID, arXiv; `/web` per gli URL. Non provati end-to-end: manca docker su questa macchina.

### 11.1 Ex bloccanti, ora chiusi

8. ~~**Il round-trip in Word e il cambio di stile.**~~ Eseguiti il 2 settembre 2026 (§12.2 e §12.3). Quattro casi su cinque come previsto; il quinto ha rivelato la duplicazione da URI irrisolto, che ha riscritto la §3.2. Il passaggio Vancouver → APA funziona su un documento che Zotero non ha mai scritto. **Il requisito originale è verificato.**
9. **Pre-render o segnaposto per gli stili numerici (§7.5).** Non più bloccante: il round-trip dimostra che il segnaposto più un Refresh funziona. Resta una scelta di rifinitura, con una raccomandazione scritta — partire dal segnaposto.

### 11.2 Da decidere, ma non bloccanti

10. **Chi possiede la promozione dallo staging.** §3.2 la lascia all'utente, che sposta a mano in Zotero. Se invece la chiave venisse allargata in scrittura sul gruppo del paper per la durata del lavoro, staging e `reconcile` non servirebbero — ma il perimetro della §2.2 diventerebbe temporaneo, e un perimetro temporaneo è un perimetro che qualcuno dimentica di richiudere.
11. **Cosa fa `catena` con un record Contrarian senza DOI**, se esiste. Nel probe tutti i record ne avevano uno, e l'intera API di Contrarian è DOI-keyed: probabilmente il caso non si presenta, ma «probabilmente» non è una specifica.
12. ~~**Le citazioni multiple in un solo campo.**~~ Deciso il 2 settembre 2026: **forma raggruppata `(1,2)`**, un campo solo con più `citationItems`, mai campi adiacenti. La regola vale in entrambe le direzioni: più identificatori dentro un unico marker — `[R:doi1; R:doi2]` quando scriviamo noi, `[Assan e Bonham, doi1, doi2]` quando lo trova la scoperta (§8.1.1) — producono un campo unico; marker distinti e adiacenti restano campi distinti. Il caso 4 del fixture ha già dimostrato che Word e Zotero rendono correttamente il raggruppamento, in Vancouver come in APA.
13. **Locator e prefissi** (`vedi cap. 3`, `cfr.`): `citation_field` li accetta come parametri, ma nel marker non hanno ancora una forma.
14. **Doppia iniezione.** Cosa succede se `catena-inject` gira due volte sullo stesso file: riconoscere i campi già presenti e saltarli, o rifiutare. Non specificato.
15. **Utenti e autenticazione.** La §10 dice «JWT in cookie httpOnly» copiando PaperTrail, ma `contrarian.borant.eu` redirige a `id.borant.eu/login`: esiste un SSO di casa che lo SPEC ignora. Da allineare, non da reinventare.
16. **Deploy e ripristino.** Come si raggiunge la translation-server, con che rete, e cosa si fa se il database dei binding si perde: i binding sono ricostruibili a mano, gli `ingest_event` no, e sono la chiave di idempotenza della §9.1.

---

## 12. Test

### 12.1 Cosa esiste

`spike/` contiene il primo strato, scritto e passato il 2 settembre 2026:

```
spike/build_fixture.py     genera catena-spike.docx: OOXML a mano, cinque casi
spike/check_fixture.py     verifica statica dello stesso file, 33 controlli
spike/fixture_items.json   tre item reali da groups/6378365 con CSL-JSON
spike/catena-spike.docx    il fixture generato
```

`build_fixture.py` non usa python-docx di proposito: il punto è controllare la forma del campo byte per byte, e una libreria di alto livello la nasconderebbe. È anche il prototipo dell'iniettore della §8 — se la struttura dei campi regge in Word, quel codice è il nucleo di `catena-inject`.

`check_fixture.py` rilegge il `.docx` **con lo stesso parser usato per analizzare il manoscritto reale**, il che è il punto: se il fixture si legge come si legge un documento prodotto da Zotero, la struttura è plausibile. Controlla il pacchetto OOXML, la buona formazione dell'XML, il bilanciamento `begin`/`separate`/`end`, la decodifica di ogni JSON di citazione, l'unicità dei `citationID`, la presenza di `uris` non vuoti e di `itemData` in ogni citationItem, i cinque casi dello spike, e la ricomposizione dei chunk `ZOTERO_PREF`.

Stato: **33 ok, 0 fail.** Un falso positivo trovato e corretto durante la scrittura — il controllo misurava la lunghezza escapata dei chunk invece di quella grezza (§7.4).

### 12.2 Il round-trip in Word — eseguito, 2 settembre 2026

`catena-spike.docx` aperto in Word con Zotero attivo, un Refresh. Esito:

| # | Caso | Atteso | Ottenuto | |
|---|---|---|---|---|
| 1 | `uris` ok, `id` stringa | `(1)` | `(1)` Assan et al. | ✓ |
| 2 | `uris` ok, **`id` assente** | `(2)`, nessun prompt | `(2)` Bonham et al., nessun prompt | ✓ |
| 3 | ripetizione dell'item 1 | di nuovo `(1)` | `(1)` Assan et al. | ✓ |
| 4 | due item in un campo | `(2,3)` raggruppati | `(2,3)` Bonham + Rosato | ✓ |
| 5 | `uris` verso key inesistente | dati incorporati, nessun prompt | `(4)` Rosato, nessun prompt — **ma voce di bibliografia duplicata** | ✗ |

Bibliografia generata: quattro voci, di cui la 3 e la 4 sono lo stesso paper (Rosato et al. 2008).

**Quattro casi su cinque come previsto.** In particolare il caso 2 conferma la §7.7 — `id` è omissibile — e i casi 3 e 4 dimostrano che numerazione e raggruppamento si comportano su campi generati da noi esattamente come su campi scritti da Zotero.

**Il caso 5 è la scoperta.** Un URI che non risolve non degrada solo il riaggancio: genera un item surrogato distinto, che prende un numero proprio e una voce di bibliografia propria. Il paper viene contato due volte. La §3.2 è stata riscritta di conseguenza, e `reconcile` da comodità è diventato un passaggio obbligato prima di condividere un documento.

Osservazione incidentale, non un difetto del fixture: la prima voce di bibliografia esce con `doi:a`, perché uno dei tre item ha il campo DOI valorizzato con la sola lettera `a`. È un dato sporco nella libreria di partenza — e il tipo di cosa che l'audit della §13.3 trova senza che nessuno la cerchi.

### 12.3 Cambio di stile — eseguito, 2 settembre 2026

Stesso file, `Document Preferences → APA`, un Refresh. Tutti e cinque i campi passano ad autore-data e la bibliografia si riformatta in APA con rientro sporgente e ordinamento alfabetico:

| # | Vancouver | APA |
|---|---|---|
| 1 | `(1)` | `(Assan et al., 2019)` |
| 2 | `(2)` | `(Bonham et al., 2009)` |
| 3 | `(1)` | `(Assan et al., 2019)` |
| 4 | `(2,3)` | `(Bonham et al., 2009; Rosato et al., 2008a)` |
| 5 | `(4)` | `(Rosato et al., 2008b)` |

**Il requisito con cui è nato il progetto è verificato sul campo:** un documento generato interamente da `catena`, senza che Zotero l'abbia mai scritto, cambia stile da Word con Document Preferences. Non è più un'inferenza dal formato delle `ZOTERO_PREF`.

E il caso 5 mostra il proprio danno meglio che in Vancouver. La duplicazione da URI irrisolto non produce solo una voce in più in bibliografia: negli stili autore-data fa scattare la **disambiguazione per anno**, e lo stesso paper compare nel testo come `2008a` e `2008b`. Sembra che Rosato abbia pubblicato due lavori quell'anno. In Vancouver era `(3)` e `(4)`, sbagliato ma silenzioso; in APA è sbagliato e visibile — il che, per una volta, è la variante preferibile.


### 12.4 Cosa serve ancora, dopo

- **Corpus di regressione:** `.docx` con tracked changes attive, con commenti, con campi Zotero preesistenti, e uno note-based (§7.6) — l'iniettore della §8 non deve toccarli e su alcuni deve rifiutarsi.
- **Test della translation-server** sul VPS, dove docker c'è: un DOI, un ISBN, un URL di rapporto istituzionale, e un identificatore inesistente. Verifica anche il tipo di item scelto (§5.2), che è la parte fragile.
- **Test di idempotenza** sulla §9.1: due `add_item` con lo stesso identificatore sullo stesso binding devono produrre un solo item Zotero.

---

## 13. Flussi

Le sezioni precedenti descrivono i pezzi. Questa descrive come si combinano nei due casi d'uso reali che hanno motivato la specifica, più quello che conviene costruire per primo.

### 13.1 Da un draft con citazioni provvisorie a una collezione nuova

*«Questo docx ha citazioni tipo `[autore, doi]`. Fai una collezione, recupera i paper, mettili come citazioni Zotero.»*

Il flusso inverte la direzione abituale: qui è il documento a generare la collezione, non il contrario.

1. `catena-inject --discover` (§8.1.1) legge il file e restituisce i candidati con le loro posizioni. Nessuna modifica.
2. `create_binding` + `create_collection` (§4.2) creano il legame e la collezione di deposito.
3. `plan_ingest` (§4.3) risolve tutti gli identificatori in un colpo e produce il piano, con gli `hint` sui cognomi come controllo incrociato (§5.0).
4. **Si legge il piano.** È l'unico punto in cui serve attenzione umana, ed è progettato per riceverla tutta insieme: cosa entra, cosa c'era già, cosa è ambiguo, cosa non si risolve.
5. `apply_ingest` esegue.
6. `catena-inject` sostituisce ogni candidato con il campo Zotero corrispondente, nella posizione registrata al passo 1, e aggiunge `ZOTERO_BIBL` più le `ZOTERO_PREF` (§7.4).
7. Refresh in Word.

Gli irrisolvibili non bloccano il flusso: restano nel documento come li aveva scritti l'autore, elencati nel report finale. Un marker che `catena` non capisce è meglio di un riferimento inventato (§5).

### 13.2 Verificare i claim di una sezione e citarne le fonti

*«Controlla il paragrafo X, verifica con Contrarian, riscrivi, aggiungi le ref nella collezione tal dei tali.»*

Qui `catena` fa una metà sola del lavoro, e la divisione è quella della §0.

1. Estrazione dei claim dal paragrafo: la fa Ono in conversazione. Non è un tool e non deve diventarlo.
2. Un run Contrarian **per claim** — l'API impone un run = un claim, quindi un paragrafo denso ne produce diversi. L'aggregazione per paragrafo non è uno stato del server: è l'insieme dei `run_id` che compaiono nel piano, e vive nel report.
3. `get_binding` sulla collezione esistente; nessuna collezione nuova.
4. `add_verified` per ogni paper sopravvissuto alla verifica, con `passages`, `bearing` e URL della traccia (§6). I verdetti `irrelevant` non passano: `citation_field` si rifiuta di produrne il campo.
5. **La riscrittura del paragrafo torna in chat.** Ono la propone, la persona la valuta e la incolla. `catena` non la vede e non la tocca.
6. `catena-inject` inserisce solo i campi delle citazioni, sul testo che è stato accettato.

I claim che chiudono `unsupported` o `no_evidence` non producono citazioni ma producono una decisione editoriale — ammorbidire la frase, o toglierla. È una decisione, e appartiene a chi firma il paper.

### 13.3 Audit di un manoscritto — sola lettura, e da costruire per primo

*«Questo paper ha le citazioni a posto?»*

Legge tutti i campi `ZOTERO_ITEM` di un `.docx` e segnala:

- URI che non risolvono più, cioè residui di staging mai riconciliati (§3.2);
- **surrogati duplicati**, lo stesso paper contato due volte perché raggiunto da due URI diversi — il difetto che in APA diventa `2008a`/`2008b` (§12.3);
- `itemData` incorporati che divergono dalla versione corrente in libreria;
- metadati palesemente sporchi: nel materiale di prova avrebbe pescato da solo l'item con `DOI = "a"`.

Non scrive niente, da nessuna parte. Funziona su qualunque documento con campi Zotero, anche scritto da altri e anni fa. Serve a `catena` per validare sé stesso, e vale già da solo — è il motivo per cui conviene che sia la prima cosa a esistere.

### 13.4 Adiacenti, riconosciuti, non a specifica

Casi plausibili, tenuti fuori finché non servono davvero. Sono annotati perché non sorprendano, non perché vadano costruiti:

- **Ricostruire la collezione da un manoscritto finito** — l'inverso di §13.1, utile quando il documento arriva da un coautore.
- **Preparazione alla submission** — copia senza field code per le riviste che li rifiutano, tenendo l'originale vivo.
- **Cambio di venue dopo un rifiuto** — PaperTrail sa già la nuova rivista; si verifica che il suo CSL esista e si aggiorna il binding. È il caso in cui i tre tool si parlano davvero.
- **Merge fra coautori** — stesso paper citato da librerie diverse: è la duplicazione della §3.2 con un'altra causa, e la stessa `reconcile` la risolve.

### 13.5 Consolidamento: metà in Zotero, metà scritto a mano

*«Questo paper ha un po' di roba collegata in Zotero e un po' scritta dentro a mano. Voglio tutte le ref verificate in una collezione sola.»*

È probabilmente il caso più frequente nella vita reale, e l'unico in cui le due popolazioni di riferimenti vanno fatte collidere prima di toccare qualsiasi cosa.

Per «verificate» qui si intende **risolvibili e pulite**: ogni citazione punta a un item Zotero vivo, con metadati corretti, presente nella collezione bersaglio. La verifica dei *claim* con Contrarian è un'altra cosa e resta la §13.2 — le due passate si compongono, ma non vanno confuse.

**1. Inventario, in sola lettura.** L'audit della §13.3 e la scoperta della §8.1.1 girano insieme sullo stesso file e producono due popolazioni:

- *campi Zotero esistenti* — per ciascuno l'`itemData` incorporato e gli `uris`, classificati in: risolve in una libreria leggibile; non risolve (orfano, locale, residuo di staging); risolve ma l'item sta fuori dalla collezione bersaglio;
- *citazioni a mano* — i candidati testuali, con o senza identificatore.

**2. Collisione fra le due popolazioni. È il passo che nessuno si aspetta.** Lo stesso paper compare spesso in entrambe: citato come campo Zotero in un punto e battuto a mano in un altro, magari perché scritto in un'altra sessione o da un coautore. Ingerire ingenuamente la versione a mano crea un secondo item, e un secondo item significa la duplicazione della §3.2 — due voci in bibliografia, e in APA un `2008a`/`2008b` che non esiste. Quindi: incrocio DOI, poi ISBN, poi titolo più anno più primo autore, **prima** di qualunque scrittura. Gli incroci incerti vanno a chi legge.

**3. Recupero degli identificatori mancanti.** Il sottocaso duro è la citazione a mano nuda: `(Rosato 2008)`, nessun DOI, nessun link. Tre fonti, in ordine:

1. **la lista di riferimenti in fondo al documento**, se c'è: è lì che vive la citazione completa, e da un titolo si risolve;
2. la collezione esistente e la libreria dell'utente, per autore e anno;
3. una ricerca per titolo.

**Nessuna delle tre accetta da sola.** La risoluzione per titolo è precisamente il modo in cui si costruisce una citazione plausibile e sbagliata — il rischio 5 della §9 — quindi produce candidati, mostra la stringa di partenza accanto al risultato, e la scelta è di chi legge. Il non risolto resta non risolto: nel report, non nel documento.

**4. La collezione bersaglio, e dove deve stare.** Tutti gli item devono finire in una collezione sola, ma quelli che esistono già stanno dove stanno. Dentro la stessa libreria basta `addto_collection`: nessuna key cambia, nessun campo esistente si rompe. **Attraverso librerie diverse no**: copiare un item genera una key nuova, quindi un URI nuovo, quindi ogni campo esistente che lo citava va riscritto.

Questa è la ragione per cui il consolidamento è il flusso in cui il modello a due gambe (§3.1) costa di più: passare per lo staging significherebbe riscrivere gli URI due volte, una all'ingresso e una alla promozione. Per questo caso la raccomandazione è l'altra opzione già prevista dalla §3.1 — **la collezione bersaglio sta nella libreria dove i riferimenti già vivono**, e la chiave viene allargata in scrittura su quel gruppo per la durata del lavoro. Con la riserva del punto 10 della §11.2: un perimetro temporaneo va richiuso, e `catena` deve ricordarlo.

**5. Normalizzazione e iniezione.** I campi esistenti con URI stantii vengono riscritti verso gli item consolidati (`--reconcile`, §3.2); le citazioni a mano diventano campi; si scrivono le `ZOTERO_PREF`; la lista di riferimenti battuta a mano in fondo viene sostituita dal campo `ZOTERO_BIBL`. Un Refresh.

**6. Criterio di uscita, e non è un'opinione.** Si rilancia l'audit della §13.3 e devono uscire tre zeri: **zero URI irrisolvibili, zero surrogati duplicati, zero citazioni il cui item non è nella collezione bersaglio.** Finché uno dei tre non è zero, il consolidamento non è finito. È l'unico flusso di questa specifica che ha una definizione di «fatto» verificabile a macchina, e conviene tenersela.

**Quello che ancora non sappiamo.** Un esemplare reale del caso misto non è stato esaminato. L'unico documento con campi Zotero nel materiale disponibile — quello della §7 — è interamente collegato — le sue 172 citazioni sono campi, e i DOI che compaiono nel testo visibile appartengono alla bibliografia renderizzata, non a citazioni battute a mano. Quindi **i pattern di riconoscimento della §8.1.1 non sono tarati su niente di vero.** Prima di implementare il passo 1 serve almeno un draft misto autentico da cui derivarli, altrimenti si scrivono espressioni regolari contro un caso immaginato.

---

## 14. Calibrazione su un draft reale

La §13.5 chiudeva dicendo che i pattern della §8.1.1 non erano tarati su niente. Il 2 settembre 2026 è arrivato un draft vero: un articolo coautorato in lavorazione, 4,4 MB. (Nemmeno questo è nel repository, per la stessa ragione: le uniche cose che restano qui sono i conteggi e le trappole.) Questa sezione è quello che se ne ricava. I numeri valgono per un documento, non per l'universo — ma un documento vero vale più di un caso immaginato.

### 14.1 Che caso è

**Non è il caso misto della §13.5: è il caso della §13.1 allo stato puro.** Zero campi Zotero — nessun `ADDIN`, nessun `instrText` in tutto il file. Tutte le citazioni sono battute a mano, e in fondo c'è una lista di riferimenti compilata a mano, sotto l'intestazione `REFERENCES (NOT FINALISED YET!)`.

In compenso porta il contorno che la §8.3 deve gestire: **11 blocchi `w:ins`, 8 `w:del` e 12 commenti**, con due autori di revisione distinti.

### 14.2 I numeri

| | |
|---|---|
| paragrafi di corpo | 90 |
| parentesi di citazione nel corpo | 123 |
| citazioni atomiche | 243, di cui 100 distinte |
| **parentesi raggruppate (con `;`)** | **56 su 123 — il 45%** |
| la più affollata | 7 riferimenti in una parentesi |
| voci nella lista di riferimenti | 90 |
| voci con DOI | 56 |
| voci con URL ma senza DOI | 5 |
| **voci senza né DOI né URL** | **29 — il 32%** |
| citazioni in-text agganciate a una voce | 95 su 100 |

Da cui la stima di rendimento per la §13.1 su questo documento:

- **61 citazioni distinte su 100 risolvibili in automatico** — hanno un DOI nella voce corrispondente e passano dritte al gradino 1 della §5;
- **34 richiedono conferma umana** — la voce c'è ma non ha DOI, quindi si risolve per titolo, e la §13.5 vieta l'accettazione automatica su base titolo;
- **5 non si agganciano a niente** e restano nel documento come le ha scritte l'autore.

Due terzi automatici e un terzo da guardare è, mi pare, il rapporto giusto: abbastanza automatico da valere la pena, abbastanza sorvegliato da non fabbricare bibliografia.

**Il 45% di parentesi raggruppate conferma la decisione sulla forma `(1,2)`** (§11.2, punto 12): non è un caso di bordo, è quasi metà del documento, e una parentesi arriva a sette riferimenti.

### 14.3 Le trappole, tutte trovate su questo file

Sono i requisiti veri del riconoscitore della §8.1.1. Ognuna ha fatto sbagliare la prima versione dell'analisi.

1. **L'intestazione della lista non è «References».** È `REFERENCES (NOT FINALISED YET!)`. Un rilevatore che cerca il titolo esatto non trova niente, e il fallback euristico aggancia la lista a metà, perdendo tutte le voci da A a B.
2. **Numerazione mista dentro la stessa lista.** 29 voci su 90 cominciano con `1. `, `8. `, `87. `; le altre no. Vanno normalizzate prima di qualunque confronto, altrimenti il cognome non è all'inizio della stringa e l'aggancio fallisce silenziosamente — è esattamente l'errore che ha prodotto un finto 83% al primo tentativo.
3. **Stili di citazione mischiati nella stessa lista.** Le prime voci sono APA (`Armour, M., Lawson, K., … (2019).`), la terza è Vancouver (`As-Sanie S, Mackenzie SC, Morrison L, et al. JAMA. 2025;334(1):64–7`). Un parser che assume una convenzione sola sbaglia su un sottoinsieme.
4. **Parentesi che sembrano citazioni e non lo sono.** 27 contengono solo cifre o solo un anno: numeri di fascicolo (`334(1)`), anni senza autore. Il filtro «deve contenere almeno tre lettere» le elimina tutte, ed è sufficiente.
5. **Citazioni narrative con prosa dentro la parentesi:** `(borrowing from Hoffmann & Tarzian, 2001)`. L'identificatore c'è ma non è all'inizio.
6. **Varianti della stessa citazione:** `Becker et al., 2022` e `Becker et al. 2022`, con e senza virgola. Vanno normalizzate o lo stesso paper entra due volte — che è la duplicazione della §3.2 per un'altra strada.
7. **Cognomi non ASCII:** `Grundström`. Ovvio finché non si scrive un pattern con `[A-Za-z]`.
8. **La trappola del parser, che non è un dettaglio.** `<w:t[^>]*>` intercetta anche `<w:tcPr>`, `<w:tab/>` e ogni altro tag che comincia per `w:t`: estrae XML al posto del testo e falsa qualunque conteggio a valle. Il pattern giusto è `<w:t(?:\s[^>]*)?>`. Costa un'ora a chi non lo sa.

### 14.4 Cosa cambia nello SPEC

- **§8.3 corretta.** Diceva che l'iniettore «non tocca `w:ins`». Sbagliato: in questo documento **una citazione sta dentro un blocco `w:ins`**, quindi il campo va inserito *conservando* la marcatura di revisione e la sua attribuzione, non evitando il blocco. Con due autori di revisione distinti, sbagliare qui significa attribuire a una persona il testo di un'altra.
- **I 12 commenti** non contengono citazioni, ma ancorano a intervalli del corpo: una sostituzione che attraversa il confine di un'ancora può spezzarla. L'iniettore la lascia stare e lo dice, invece di provarci.
- **La §13.5 resta senza esemplare.** Questo è un draft tutto-a-mano, non un misto: il caso «metà in Zotero e metà a mano» continua a non avere un file vero su cui tararsi. La differenza non è cosmetica — nel misto il passo che conta è la collisione fra le due popolazioni, e qui una delle due popolazioni è vuota.

### 12.5 La catena intera, verificata dal vivo — 3 settembre 2026

Non più pezzi isolati: il giro completo, dalla superficie MCP a Word.

`create_collection` crea una collezione nel gruppo di deposito; `create_binding`
lega un paper a due gambe reali risolvendo i nomi umani in chiavi Zotero;
`collection_items` legge la gamba di lettura e confronta le due; `citation_field`
produce un campo raggruppato di due item e uno singolo con locator;
`document_prefs` restituisce le due proprietà, 255 e 50 caratteri — la stessa
spezzatura che Zotero aveva prodotto nel manoscritto della §7.

Quelle uscite sono state impacchettate in un `.docx` **senza che nessuna chiave,
nessun CSL-JSON e nessun URI passasse per una trascrizione a mano**, poi:

- l'audit della §13.3 lo ha riletto con **zero errori** — le due metà del
  formato, quella che scrive e quella che legge, d'accordo su un documento
  contro cui nessuna delle due era stata scritta;
- aperto in Word con Zotero attivo e sottoposto a Refresh, si è formattato
  correttamente: raggruppamento e locator inclusi.

**È la catena del nome, chiusa.** Da una collezione Zotero a un campo formattato
in Word, senza un solo passaggio in cui qualcuno ricopia qualcosa.

Resta fuori il ramo di ingest — `resolve_identifier`, `add_item`, `add_verified`,
`plan_ingest` — che ha bisogno della translation-server, e quindi del VPS
(§5). E resta l'iniettore: oggi il `.docx` viene assemblato da zero, mentre la
§8 chiede di inserire i campi in un documento che esiste già, conservandone
revisioni e commenti.
