# Beitragen zu Chronicle

Danke, dass du mitmachen möchtest. Dieses Dokument beschreibt, wie wir arbeiten. Der Projektstand und die offenen Punkte stehen in der [README](README.md) und in den [Issues](https://github.com/JoePhanDro/Chronicle-as-Code/issues).

## Arbeitsweise

- **`main` ist geschützt.** Direkte Pushes sind nicht möglich. Änderungen laufen über Pull-Requests.
- **Ein Merge erfolgt von Hand.** Auto-Merge ist nicht aktiviert. Wer den Merge-Button klickt, ist verantwortlich.
- **CI muss grün sein.** Die Checks „Tests & smoke build“ und „Docker build“ sind Pflicht und müssen auf dem aktuellen Stand von `main` laufen.
- **Ein Issue pro Thema.** Größere Änderungen starten mit einem Issue, bei Architekturfragen mit einem ADR (siehe unten).
- **Kleine PRs.** Ein PR löst ein Thema. Lieber drei kleine PRs als einer mit 2000 Zeilen.

## Branches und Commits

- Branch-Namen: `feature/<kurz>`, `fix/<kurz>`, `docs/<kurz>`, `security/<kurz>`, `dependabot/...` (von Dependabot angelegt).
- Commit-Nachrichten im Imperativ, erste Zeile unter etwa 72 Zeichen. Der Body erklärt das Warum, wenn es nicht offensichtlich ist.
- Bezüge zu Issues im Body: `Refs #22` (teilweise) oder `Fixes #22` (vollständig).

## Pull-Requests

1. Branch von aktuellem `main` anlegen.
2. Tests lokal laufen lassen (`pytest`), bei Template-Änderungen `npm run build:css`.
3. PR öffnen. Die Vorlage bringt eine Checkliste mit. Ungeprüfte Punkte bitte offen lassen und benennen.
4. Wenn `main` sich inzwischen geändert hat, den Branch aktualisieren (GitHub: „Update branch“). Die Protection verlangt das.
5. Auf die CI warten. Rote Checks zuerst beheben, bevor jemand reviewt.

## Code

- **Python 3.12**, Typ-Hinweise überall, wo sie den Code lesbarer machen.
- **Code und Bezeichner auf Englisch.** Nutzertexte, Fehlermeldungen und Dokumentation auf Deutsch, das ist das Projekt-Standard.
- **Kommentare sparsam.** Sie erklären das Warum, nicht das Was.
- **Datenbankzugriffe immer parametrisiert.** Keine Zeichenketten mit Benutzereingaben in SQL zusammensetzen. Die einzige Ausnahme sind feste Spaltennamen im Code, siehe `accounts.update_user`.
- **Fehler mit Status-Codes** kommen aus der Repository-Schicht (`NotFound`, `Conflict`, `Invalid`) und werden in der API oder Oberfläche übersetzt.
- **Neue Eingabefelder** müssen über `models.py` (YAML) oder `event_input.py` (Schreibzugriffe) laufen. Keine eigene Validierung in Routen.

## Tests

- Jede Verhaltensänderung braucht einen Test. Fehlerpfade gehören dazu (falsche Rolle, fehlende CSRF, Konflikt, ungültige Eingabe).
- Tests laufen ohne Netzwerk und ohne Docker.
- Browser-Tests (`tests/ui/`) laufen nur mit `pytest -m browser`. Einmalig `python -m playwright install chromium` ausführen (oder mit `CHRONICLE_BROWSER_CHANNEL=chrome` eine installierte Google Chrome nutzen). Das Dashboard lädt seine Bibliotheken per CDN, deshalb braucht der Dashboard-Test Netzwerk.
- Tests nutzen temporäre Datenbanken (`tmp_path`), nie `./chronicle.db`.

## Sicherheit

- **Keine Sicherheitslücken als öffentliches Issue melden.** Bitte den Repository-Owner direkt kontaktieren (GitHub-Profil von [@JoePhanDro](https://github.com/JoePhanDro)).
- Geheimnisse gehören nicht ins Repository. `.env`, Datenbanken und Evidence-Dateien mit echten Inhalten sind in `.gitignore` bzw. gehören nicht hinein.
- Neue Abhängigkeiten auf feste Versionen pinnen. Dependabot prüft wöchentlich.
- Base-Images im Dockerfile sind per Digest gepinnt. Ein Update ist eine bewusste Änderung.
- CDN-Skripte brauchen einen `integrity`-Hash. Bei Versionswechsel den Hash im Template mit aktualisieren.

## Architekturentscheidungen

Entscheidungen, die mehrere Teile betreffen oder schwer rückgängig zu machen sind, halten wir als ADR in [`docs/adr/`](docs/adr/) fest:

1. Nimm die nächste freie Nummer in `docs/adr/` (aktuell ist `0003` die letzte).
2. Kopfzeilen: Titel, Status (`Vorgeschlagen`, `Akzeptiert`, `Ersetzt durch ...`), Datum, Bezug, dann Kontext, Entscheidung, Konsequenzen und offene Punkte. Vorlage: ADR 0002.
3. Der ADR kommt in den gleichen PR wie die Umsetzung oder vorher als eigener Docs-PR.

## Dokumentation

- README ist der Einstieg. Sie beschreibt den Ist-Zustand. Wer ein Feature ändert, passt die README im selben PR an.
- Die API-Tabelle und die Rollenmatrix in der README müssen zu den Routen passen.

## Fragen

Offene Fragen gehören in ein Issue mit dem Label `question`. Wenn etwas unklar ist, lieber fragen als raten.
