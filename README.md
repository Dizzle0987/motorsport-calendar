# Motorsport Calendar

Calendari iCalendar sottoscrivibili per Formula 1, MotoGP e un feed combinato. Il progetto usa `Europe/Rome`, UID indipendenti da data e ora, `SEQUENCE` incrementale, override manuali e scritture atomiche.

## Feed pubblici

- Completo: `https://dizzle0987.github.io/motorsport-calendar/calendar.ics`
- Formula 1: `https://dizzle0987.github.io/motorsport-calendar/f1.ics`
- MotoGP: `https://dizzle0987.github.io/motorsport-calendar/motogp.ics`

Per sottoscrivere direttamente, sostituire `https://` con `webcal://`. Una sottoscrizione riceve gli aggiornamenti; un download/importazione singola è una copia statica.

## Fonti e criteri

Le date dei round iniziali provengono dai calendari ufficiali [Formula1.com](https://www.formula1.com/en/racing) e [MotoGP.com](https://www.motogp.com/en/calendar). L’updater legge le pagine ufficiali F1 delle singole gare e l’API pubblica ufficiale MotoGP per sostituire le sessioni giornaliere con data, ora di inizio e ora di fine. Jolpica/Ergast fornisce un fallback strutturato per gli orari F1 quando una pagina ufficiale non è raggiungibile dal workflow; la fonte effettiva resta registrata in ogni evento. Soltanto quando nessuna fonte pubblica un orario viene mantenuta una voce giornaliera e non viene inventata un’ora.

## Stagioni future

Ogni aggiornamento controlla automaticamente la stagione corrente e quella successiva. Appena Formula 1 o MotoGP pubblicano un nuovo calendario strutturato, i round vengono aggiunti a `data/rounds.json`, trasformati in sessioni e inclusi negli stessi tre URL iCalendar già sottoscritti. Non è necessario sottoscrivere un nuovo link ogni anno.

Durante l’anno restano disponibili la stagione corrente e quelle future già annunciate. Dal 1° gennaio, non appena il calendario della nuova stagione è disponibile, la stagione precedente viene rimossa automaticamente dal catalogo, dagli eventi e dai tre feed; gli URL di sottoscrizione non cambiano. La pulizia avviene separatamente per Formula 1 e MotoGP, quindi un ritardo o un errore di una fonte non può svuotare una delle due serie. L’anno fa parte dell’identità logica del Gran Premio, quindi due edizioni annuali non condividono lo stesso UID. Test pre/post-stagionali e sessioni straordinarie restano esclusi.

I palinsesti Austria e Italia sono conservativi. Senza una pagina che indichi esplicitamente sessione, data e ora, il campo resta “Da confermare”; ORF e ServusTV non vengono presentati insieme come se trasmettessero la stessa sessione. TV8 in differita non viene mai presentato come diretta. Lo streaming può essere soggetto a limitazioni geografiche.

## Uso locale

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m motorsport_calendar.cli        # aggiorna anche dalle fonti online
python -m motorsport_calendar.cli --offline
pytest
```

La generazione crea e valida prima tutti gli output, poi li sostituisce atomicamente. Se una fonte fallisce, usa le date ufficiali incluse e conserva comunque l’ultimo output valido; non pubblica calendari vuoti o privi di una delle due competizioni.

## Eventi e correzioni manuali

`data/events.json` è l’archivio risultante e conserva fonti, broadcaster, conflitti, UID e sequenza. `data/manual_events.json` ha priorità sui dati automatici.

Esempio di override:

```json
{
  "competition": "Formula 1",
  "grand_prix": "FORMULA 1 EXAMPLE GRAND PRIX 2026",
  "session": "Gara",
  "circuit": "Example Circuit",
  "location": "Example City",
  "country": "Italy",
  "start": "2026-09-06T15:00+02:00",
  "status": "programmata",
  "broadcaster_at": "ORF 1 / ORF ON",
  "broadcaster_at_url": "https://tv.orf.at/",
  "broadcast_type_at": "diretta",
  "broadcaster_it": "TV8",
  "broadcaster_it_url": "https://www.tv8.it/guidatv",
  "broadcast_type_it": "differita",
  "source_sport": "Formula1.com",
  "source_sport_url": "https://www.formula1.com/",
  "source_time": "Formula1.com",
  "source_time_url": "https://www.formula1.com/",
  "stable_key": "formula-1:example-grand-prix:gara"
}
```

La `stable_key` è facoltativa, ma è consigliata per correzioni e rinvii: deve restare uguale anche se l’evento viene spostato di mesi. Per rimuovere una voce automatica, ripetere la sua `stable_key` con `"enabled": false`. Gli stati ammessi sono `programmata`, `da confermare`, `rinviata`, `cancellata`, `conclusa`.

## Promemoria

Tutte le sessioni hanno un avviso 2 ore e 30 minuti prima. Le gare principali hanno anche un avviso 24 ore prima. Su Apple Calendar, nelle impostazioni della sottoscrizione, lasciare disattivato **Rimuovi avvisi**.

## Automazioni GitHub

- `update.yml`: ogni 6 ore o manualmente, scoperta delle stagioni corrente/successiva, test prima e dopo la generazione, commit solo per cambiamenti reali, concorrenza serializzata.
- `pages.yml`: riesegue i test e pubblica la root del repository su GitHub Pages.

Le Actions utilizzate sono le generazioni correnti basate su Node.js 24 (`checkout@v5`, `setup-python@v6` e versioni Pages correnti).

Per attivare Pages: **Settings → Pages → Source: GitHub Actions**.

## Licenza

MIT. Marchi e dati appartengono ai rispettivi titolari; questo progetto non è affiliato a Formula 1, FIA, MotoGP, FIM o broadcaster.
