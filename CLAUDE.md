# CLAUDE.md — catena

*Istruzioni per chi lavora su questo repository con Claude Code.*

## Prima di toccare autenticazione, rotte, Caddy, MCP, faccia o lingue

**Leggi le convenzioni dei tool borant prima di scrivere, non dopo.** Sono
scritte per intero in una pagina sola, e si raggiungono in due modi:

- se hai il repository `Ono3` sotto mano:
  `wiki/projects/strumenti/convenzioni-tool-borant.md`
- altrimenti, da qualunque macchina: l'MCP **`onopedia`**,
  `get_page("convenzioni-tool-borant")`

La versione operativa col codice sta in `borant-id/SPEC.md` §20, **gitignorata**:
esiste solo sul disco di chi ha quel clone.

Le sezioni che toccano questo repository sono autenticazione (`local` è il
default, la chiave è il `subject` e mai l'email), path pubblici, la vetrina e la
home (`/` vetrina che non guarda chi la legge, `/app` gated), fallire chiusi
invece di rimbalzare sul login, la superficie MCP con le cinque cose che si
rompono ogni volta, faccia e lingue.

**Perché sta scritto qui.** Il 3 settembre 2026 una sessione ha ricostruito nove
di quelle convenzioni deducendole dal codice invece di leggerle — il commit si
chiama così — e ha indovinato quasi tutto. «Quasi», su autenticazione e path
pubblici, è il modo in cui nasce aperta una rotta che doveva nascere chiusa.

**Non ricopiare le convenzioni qui dentro.** Due copie divergono, e quella
sbagliata è sempre la più vicina.

## Le tre fonti di questo repository

- `README.md` — l'uso
- `DEPLOY.md` — il server
- `SPEC.md` — il perché. **Gitignorata dal 3 settembre 2026**, perché contiene
  l'analisi di sicurezza e le decisioni scartate, che non vanno in un repository
  pubblico. Se non ce l'hai, chiedila: non arriva con un `git pull`.

## Il blocco Caddy non si scrive a mano

È generato: `python caddy.py --gated` legge `PUBLIC_PATHS` in
`src/catena/server/main.py`. La lista dei path pubblici vive in un posto solo,
così chi aggiunge una rotta pubblica se ne accorge mentre la scrive. Dopo aver
toccato le rotte, rigenera e confronta con quello che gira in produzione.
