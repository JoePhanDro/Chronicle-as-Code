# ADR 0020: Anhänge im Vault

- **Status:** Entschieden vom Projektinhaber am 2026-10-27: **Anhänge liegen in Git** (Option 2). Große Binärdateien bleiben draußen und werden verlinkt (Punkt 7). **Ändert eine bestätigte Entscheidung** (ADR 0008 Punkt 6, ADR 0009). GitHub ist der Vorfallsspeicher, in eigenen Vorfall-Repositories der Betreiber. **Tarif, Vertrag zur Auftragsverarbeitung, Region und Aufbewahrung entscheidet der Betreiber**, nicht das Projekt. Chronicle liefert Prüfungen und Hinweise (ADR 0016, Punkt 10, ADR 0021), erzwingt sie aber nicht.
- **Datum:** 2026-10-25
- **Bezug:** ADR 0004 (Evidence), ADR 0008 Punkt 6, ADR 0009 (Abschnitt Anhänge), ADR 0013, ADR 0021, `chronicle/evidence.py`
- **Ändert:** ADR 0008 Punkt 6 und ADR 0009, Abschnitt „Anhänge“ („außerhalb von Git, im Sync-Ordner“).

## Kontext

ADR 0009 hat entschieden: Anhänge liegen außerhalb von Git, und „Git LFS wird neu bewertet, sobald eine zweite Person mitarbeitet“. Genau dieser Fall tritt jetzt ein. Zusätzlich:

- Ein Prüfer sieht in einem Pull Request nur, was im Repository liegt. Anhänge im Sync-Ordner sind **nicht freigebbar** und nicht an die Änderung gebunden.
- Mehrere Einsatzorte mit je einem Sync-Dienst (OneDrive o. ä.) vervielfachen den Betrieb und die Datenabflüsse.
- Die CI kann Anhänge nicht prüfen (ADR 0009: Option `--ohne-anhaenge`).

Dagegen steht: Was in Git liegt, bleibt in der Historie (ADR 0021), und Anhänge (Screenshots, Logs) sind das Material, in dem am ehesten Geheimnisse und personenbezogene Daten stecken.

## Optionen

1. **Außerhalb von Git** (bisher). Nicht freigebbar, Betrieb eines zweiten Dienstes, kein Zusammenhang zur Änderung.
2. **Im Vault als normale Git-Dateien.** Freigebbar, in einem Klon, zusammen mit der Änderung. Repository wächst. Entfernen nur durch Umschreiben der Historie.
3. **Im Vault mit Git LFS.** Das Repository bleibt klein. Hoster rechnen Speicher und Bandbreite ab (siehe ADR 0009 mit Stand und Preisen). Jede LFS-Version bleibt erhalten, für Löschen gibt es nur den Support des Hosters. Zusätzliche Abhängigkeit (`git-lfs`) und ein weiterer Authentifizierungsweg.
4. **Eigener Speicher (Objektspeicher) mit Prüfsummen im Vault.** Kein Review der Datei selbst, dafür Löschbarkeit. Mehr Infrastruktur, eigener Zugriffsschutz nötig.

## Entscheidung

**Option 2, mit einem Ausweg.**

1. **Anhänge liegen im Vault** unter `evidence/<event-key>/<dateiname>` (Pfade wie in Format 0, `docs/notation.md` Abschnitt 2). Sie sind normale Git-Dateien.
2. **`sha256` ist Pflicht** je Anhang in der Event-Datei (`docs/notation.md`, Abschnitt 10 Vorschlag wird Pflicht in Format 1). Die Prüfung vergleicht die Datei mit der Prüfsumme. Damit ist der Anhang an genau diese Fassung der Änderung gebunden, und Prüfer sehen ihn zusammen mit der Event-Datei.
3. **Regeln aus ADR 0004 gelten unverändert:** Endung und Inhalt passen zusammen, SVG und HTML verboten, höchstens 10 MiB je Datei. Zusätzlich:
   - **Verwaiste Dateien** (unter `evidence/`, von keinem Event genannt) und **fehlende Dateien** sind Prüffehler.
   - **Gleicher Inhalt zweimal** ist erlaubt (Git speichert Blobs einmal).
   - Die Prüfung warnt ab 200 MiB Gesamtgröße von `evidence/` und meldet einen Fehler ab 1 GiB. Das Manifest darf diese Werte nur senken (ADR 0013), die Obergrenzen stehen im Code. Die Zahlen sind Vorschläge.
   - **Git darf Anhänge nicht verändern.** Zeilenenden-Umwandlung, `.gitattributes` mit `text=auto`, `eol`, `ident` oder `filter` ändern die Bytes und damit `sha256`. Gegenmaßnahmen: pro Aufruf `core.autocrlf=false`, `core.eol=lf`, `core.safecrlf=false`, in `.git/info/attributes` `evidence/** -text -ident -filter binary`, und `.gitattributes` im Vault enthält nur die Zeilen der Vorlage (ADR 0022, Punkte 6 und 22).
4. **Anhänge werden nicht verändert.** Eine neue Fassung ist eine neue Datei (Nummer im Namen, wie heute) und eine neue Prüfsumme. Das Entfernen eines Anhangs entfernt die Datei und den Eintrag.
5. **Der Ausweg:** Das Manifest darf später `evidence.storage: lfs` nennen. Dann steht in `.gitattributes` `evidence/** filter=lfs …`, und die Anwendung verlangt `git-lfs`. Der Umstieg ist eine Formatänderung (ADR 0023) und wird nicht beiläufig eingeführt. Bis dahin ist LFS nicht Teil des Plans.
6. **Cloud-Sync-Ordner entfallen** als Weg für Anhänge, die ein Prüfer sehen soll. Wer sie weiter verwendet, tut es außerhalb von Chronicle. Der Hinweis in ADR 0008 zu Konfliktkopien (`waf-block (1).png`) gilt nur noch für Altbestand.

7. **Große Binärdateien kommen nicht in den Vault, sie werden verlinkt (Entscheidung des Projektinhabers).** Wer eine Datei über der Grenze hat (Speicherabbild, Mitschnitt, Video), legt sie am Speicherort der Organisation ab, etwa OneDrive oder SharePoint, und trägt im Event einen Link ein statt einer Datei:
   - Ein Anhang hat **entweder** `file` (liegt im Vault, mit `sha256`) **oder** `link` (liegt außerhalb), nie beides. Beschreibung und Typ bleiben Pflicht, `sha256` der externen Datei ist optional, damit der Stand nachvollziehbar bleibt. Der Server kann sie nicht prüfen.
   - Zulässig sind nur `https://`-Adressen, und nur von Servern, die das Manifest in `evidence.link_hosts` nennt (z. B. `*.sharepoint.com`, `onedrive.live.com`). Ohne Eintrag sind Links verboten, damit ein Tippfehler oder ein fremder Link nicht stillschweigend durchgeht.
   - Der Link wird nur **angezeigt**: `rel="noopener noreferrer"`, kein Einbetten, keine Vorschau, **kein Abruf durch den Server** (kein SSRF, kein Dateiabgleich). Der Server liefert ihn nicht als Anhang aus.
   - Die Zugriffsrechte liegen beim Speicherort, nicht bei Chronicle. Ein Link „jeder mit dem Link“ ist ein Datenabfluss und wird vom Vorabscan (ADR 0022, Punkt 19) gewarnt, ebenso eine Adresse mit Zugriffsschlüssel in der Abfrage (`?sig=`, `?token=`).
   - Ein Link kann ins Leere zeigen, wenn die Datei verschoben oder gelöscht wird. Die Prüfung meldet das nicht. Beim Löschen eines Events (ADR 0021) bleibt die externe Datei bestehen und muss dort entfernt werden.
8. **Erlaubte Dateien im Vault:** Bilder (PNG, JPEG, GIF, WebP), PDF, Text und Log (`.txt`, `.log`) und **Markdown (`.md`, neu)**, jeweils bis 10 MiB (ADR 0004). Markdown-Anhänge werden wie Text ausgeliefert (`text/plain`, nie als HTML dargestellt, `X-Content-Type-Options: nosniff`) und in der Oberfläche als Text angezeigt. Andere Typen bleiben verboten. Die Grenze von 10 MiB gilt je Datei und ist die Obergrenze für „normales Git“. Alles darüber ist ein Link.

## Konsequenzen

- Der Klon enthält alle Anhänge. Erste Klone und Fetches dauern länger als ohne. Teil-Klone (`--filter=blob:limit`) machen den Zugriff netzabhängig und sind nicht vorgesehen.
- **Betreiber sollten vor dem ersten Einsatz mit echten Vorfallsdaten** Hoster, Tarif, Vertrag zur Auftragsverarbeitung, Region und Bereinigung (ADR 0021) geklärt haben. Danach lässt sich ein Anhang praktisch nicht mehr sicher aus allen Kopien entfernen. Die Dokumentation sagt das ausdrücklich, die Anwendung macht es nicht zur Bedingung.
- Die Prüfung in der CI (ADR 0022) kann jetzt auch Anhänge und Prüfsummen prüfen. `--ohne-anhaenge` bleibt als Option für Vaults mit `evidence.storage` anders als `git`.
- GitHub lehnt Dateien über 100 MiB beim Push ab und rät zu Repositories unter etwa 1 GiB. Das 10-MiB-Limit liegt weit darunter.
- Der Server liefert Anhänge unverändert über den autorisierten Endpunkt aus (ADR 0004, `delivery_headers`). Pfade werden als `Vault-Wurzel / relativer Pfad` aufgelöst, ohne Symlinks (ADR 0022).

## Offene Punkte

- ~~Ob Anhänge von Anfang an in LFS liegen sollen.~~ Entschieden: nein. Große Dateien werden verlinkt (Punkt 7), kleine liegen in Git. LFS bleibt der spätere Ausweg (Punkt 5).
- GitHub-Tarif, Vertrag zur Auftragsverarbeitung und Region: Sache des Betreibers (Entscheidung des Projektinhabers, 2026-10-27), kein offener Punkt des Projekts.
- Die Liste `evidence.link_hosts` und der Umgang mit Links ist eine neue Formatfunktion und gehört in Format 1 (ADR 0013, Plan Teil 6b).
