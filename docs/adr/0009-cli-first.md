# ADR 0009: CLI als Kernprodukt, Server als Oberfläche

- **Status:** Akzeptiert für den CLI-Teil. Der Abschnitt „Anhänge“ ist durch ADR 0019 geändert (Entscheidung des Projektinhabers am 2026-10-27): Anhänge liegen in Git, große Binärdateien werden verlinkt, Git LFS bleibt nur ein späterer Ausweg.
- **Datum:** 2026-10-22
- **Bezug:** ADR 0001 (Server), ADR 0002 (Topics und Events), ADR 0003 (wird durch ADR 0008 ersetzt), ADR 0004 (Evidence), ADR 0005 (Export und Backup), ADR 0008 (Dateien als Quelle der Wahrheit), `docs/notation.md`. Issues #85, #87, #89, #90

## Kontext

Chronicle soll „as code“ sein. Ein Stand ist ein Ordner aus YAML-Dateien (ADR 0008). Bisher hängen Prüfung, Import und Export an der Datenbank und am Server. In `repository.py` mischen sich Schreiblogik und Prüfung. Damit lässt sich ein Stand nur mit laufendem Server prüfen, aufbauen oder in der CI testen.

## Entscheidung

1. **Die Bibliothek `chronicle` enthält Schema, Prüfung, Index und Rendering.** Sie hängt nicht vom Server ab.
2. **Die CLI ist das Kernprodukt.** Befehle:
   - `chronicle check` prüft die Dateien und schreibt nichts.
   - `chronicle plan` zeigt, welche Events angelegt, geändert oder entfernt würden.
   - `chronicle apply` übernimmt den Stand in den Index.
   - `chronicle build` erzeugt Berichte und statische Ansichten.
3. **Server und Oberfläche rufen dieselben Funktionen auf.** Sie enthalten keine eigene Fachlogik.
4. **Die Notation in `docs/notation.md` ist maßgeblich.** CLI und Server richten sich nach ihr.
5. **Die bestehenden Befehle `importer` und `exporter`** bleiben als Aufrufe erhalten, bis die CLI sie ersetzt.

Die Analogie ist Terraform: Die CLI ist die Wahrheit, die Anwendung ist eine Sicht darauf.

## Konsequenzen

- Prüfung und Schreiblogik in `repository.py` werden getrennt. Der erste Schritt ist #85.
- Die CLI läuft ohne Docker. Die CI ruft sie direkt auf.
- Fachlogik lässt sich ohne Server testen.
- Ein späterer Befehl `chronicle draft` (LLM-Entwürfe) würde Vorschläge erzeugen, die `check` prüft. Er ist nicht Teil dieser Entscheidung.

## Anhänge

Entschieden: Anhänge liegen vorerst außerhalb von Git, im Sync-Ordner (ADR 0008, Punkt 6). Git LFS wird neu bewertet, sobald eine zweite Person mitarbeitet.

Begründung: Git LFS ist kostenlos, der Hoster rechnet aber ab. Bei GitHub sind im Free- und Pro-Tarif 10 GiB Speicher und 10 GiB Bandbreite pro Monat enthalten, danach kostet GiB Speicher 0,07 $ und GiB Bandbreite 0,0875 $ ([GitHub-Dokumentation zur LFS-Abrechnung](https://docs.github.com/en/billing/using-the-new-billing-platform/about-billing-for-git-large-file-storage)). Das Risiko ist die Bandbreite: Jeder frische Klon lädt alle Anhänge. Außerdem bliebe jede Version eines Anhangs dauerhaft in der Historie. Stand: 2026-10-22.

Folge: Die CI kann Evidence-Dateien nicht prüfen, weil sie nicht im Repository liegen. Die Prüfung braucht deshalb eine Option, die Anhänge nicht verlangt. Die CI prüft dann die YAML-Struktur, die Verweise und das Format der Prüfsummen. Ob ein Anhang existiert und zu seiner Prüfsumme passt, prüft die lokale Prüfung.

## Offen

- **Merge-Treiber auf Feldebene:** Welche Felder einzeln zusammengeführt werden.
- **Oberfläche:** htmx oder das bestehende JavaScript.
