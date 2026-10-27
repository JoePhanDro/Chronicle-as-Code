# ADR 0016: Freigabe-Workflow mit Branch, Pull Request und Branch Protection

- **Status:** Vorgeschlagen (zu bestätigen). Überarbeitet nach Review. **Entscheidung des Projektinhabers am 2026-10-27: kein Live-Modus und kein Incident-Branch** (Punkte 12 bis 14). Das Vier-Augen-Prinzip bleibt unverändert, Plan-Teil 19 entfällt.
- **Datum:** 2026-10-25
- **Bezug:** ADR 0008 Punkt 3 (Abgleichen), ADR 0013, ADR 0015, ADR 0017, ADR 0021, ADR 0022, `chronicle/sync.py`, `.github/CODEOWNERS`, Issues #90, #131
- **Ändert:** ADR 0008 Punkt 3: „Abgleichen“ schreibt nicht mehr auf den gemeinsamen Branch, sondern auf einen Arbeits-Branch.

## Kontext

Heute committet `sync.synchronise` Änderungen und pusht sie direkt auf den ausgecheckten Branch (`HEAD:refs/heads/<branch>`). Das ist für eine Person gedacht. Mehrere Personen sollen Änderungen gegenseitig freigeben. Gleichzeitig muss während eines Vorfalls schnell dokumentiert werden können.

## Optionen

1. **Direkt auf `main` pushen** (heutiger Stand). Keine Freigabe.
2. **Pull Request je Änderung, Freigabe auf dem Hoster.** Bewährt, Review-Werkzeuge sind da, Historie nachvollziehbar.
3. **Eigener Freigabe-Status in den Dateien** (`approved_by:`). Fälschbar, weil jede Person die Datei ändern kann. Verworfen.
4. **Eigene Freigabe-Oberfläche in der App.** Doppelt gebaut, nur vertrauenswürdig, wenn der Hoster sie erzwingt. Verworfen.

## Entscheidung

**Option 2**, mit einem Zustandsautomaten für den Arbeitsstand, einem Live-Zweig für Vorfälle (Punkte 12 bis 14, Entscheidung offen) und ausdrücklichen Voraussetzungen auf dem Hoster.

### Arbeitsstand

1. **Die App committet nie auf den Standard-Branch.** Sie arbeitet auf einem **Arbeits-Branch** `chronicle/<klon-kennung>/<datum>-<kurzname>`. Die **Klon-Kennung** ist eine zufällige Kurzkennung, die `vault add`/`vault clone` für diesen Klon erzeugt und in der Registry ablegt (nicht der Benutzername). Es gibt je Klon einen offenen Arbeits-Branch. Wer mehrere Benutzer an einer Installation hat, schreibt trotzdem auf diesen einen Branch; **der Autor jedes Commits ist der angemeldete App-Benutzer** (ADR 0015). Es ist **höchstens eine Schreibsitzung** je Klon aktiv. Eine zweite Sitzung kann lesen, aber nicht schreiben, bis die erste endet (Sperre mit Kennung der Sitzung, nicht nur Dateisperre).
2. **Jedes Speichern ist ein lokaler Commit.** Das gibt Rückgängig-Möglichkeit und passt zu „alles oder nichts“ (ADR 0008). Die Nachricht nennt Aktion und Key.
3. **Die Oberfläche liest den Arbeitsstand** (ausgecheckte Arbeitskopie). Wer dokumentiert, sieht das Ergebnis sofort.
4. **Freigabestatus je Event über die Blob-ID.** Ein Event ist **„freigegeben“**, wenn seine Datei in `origin/<standard>` mit **gleicher Blob-ID** (und gleichem Pfad) steht wie im Arbeitsstand. Berechnet aus `git ls-tree -r -z` beider Stände, ohne Netz nach einem `fetch`. Dieser Vergleich ist unabhängig davon, wie der Pull Request zusammengeführt wurde (Squash, Rebase, Merge-Commit). Ein Vergleich über Commits oder `git diff origin/main HEAD` allein trägt nach einem Squash-Merge nicht, weil der Arbeits-Branch dann nicht als „gemergt“ gilt (im Review belegt).

### Lebenszyklus eines Change-Sets (Zustandsautomat)

5. Ein Change-Set ist der Arbeits-Branch mit seinen Commits seit `origin/<standard>`.

   | Zustand | Bedeutung | Übergang |
   |---|---|---|
   | **offen** | lokale Commits, nichts gepusht | „Einreichen“ → eingereicht |
   | **eingereicht** | Remote-Branch hat denselben Stand wie lokal | neuer Commit → geändert nach Einreichen; PR auf dem Hoster gemergt und „Aktualisieren“ ergibt gleichen Inhalt → abgeschlossen |
   | **geändert nach Einreichen** | lokaler Stand ≠ Remote-Branch | „Einreichen“ → eingereicht |
   | **abgeschlossen** | nach „Aktualisieren“ gilt `git diff --quiet origin/<standard> HEAD` für alle Pfade des Change-Sets (inhaltlich enthalten, **nicht** „Commit ist Vorfahr“) | Arbeits-Branch wird lokal entfernt, nächster Arbeitsstand beginnt von `origin/<standard>` |
   | **verworfen** | Benutzer wählt „Arbeitsstand verwerfen“ | lokaler Branch wird auf `origin/<standard>` zurückgesetzt (nach Bestätigung, mit Liste der verlorenen Commits; Sicherung als Reflog-Eintrag und Tag `chronicle-discarded/<datum>`), Remote-Branch bleibt für den Hoster unberührt |
6. **Remote-Branch ist weitergewandert** (zweiter Klon desselben Benutzers, Änderungsvorschlag eines Prüfers): Der Push würde nicht schnell vorspulen. Die App holt den Remote-Branch, **führt ihn in den lokalen Arbeits-Branch ein** (Merge, mit Treiber, ADR 0017) und pusht erneut. Es gibt **nie `--force`**.
7. **„Aktualisieren“** holt nur die benötigten Refs (`+refs/heads/<standard>:refs/remotes/origin/<standard>` und den eigenen Arbeits-Branch, ADR 0022 Punkt 8), setzt den lokalen Standard-Branch per `merge --ff-only` vor und führt `origin/<standard>` in den Arbeits-Branch ein.
8. **„Zur Freigabe einreichen“** prüft die Dateien (`check`), aktualisiert wie Punkt 7, pusht **nur den Arbeits-Branch** (`push -u origin <branch>`) und zeigt den Link zum Pull Request. Die Linkvorlage steht je Vault in der Registry (`compare_url = "https://github.com/{org}/{repo}/compare/{base}...{head}?expand=1"`), damit sie bei anderen Hostern anpassbar ist. Die App legt in der ersten Ausbaustufe **keinen Pull Request über die API** an und braucht **keinen Token**.

### Voraussetzungen und Einstellungen auf dem Hoster

9. **Pflicht** (der Vault-Administrator richtet sie ein):
   - Standard-Branch geschützt, Änderungen nur über Pull Request, Force-Push und Löschen verboten,
   - mindestens eine Freigabe (Vorgabe der Vorlage; 0 nur bei Einzelpersonen, siehe offene Frage 3), Freigabe der Code-Owner verlangt, veraltete Freigaben bei neuen Commits verwerfen, Freigabe des letzten Pushs durch eine andere Person,
   - der Status `Chronicle prüfen` ist erforderlich; der Workflow läuft für `pull_request` **und `merge_group`**, wird nie durch `paths-ignore` oder `if:` übersprungen (ein übersprungener Job zählt als bestanden),
   - **„Branch muss aktuell sein“ ist Pflicht** (oder eine Merge Queue). Nur so entsteht kein Duplikat-Key aus zwei einzeln grünen PRs (ADR 0013),
   - Änderungen an `.github/` gehören einer kleinen Gruppe (CODEOWNERS), zusätzlich ein Regelwerk der Organisation, das ein Vault-Repository nicht selbst aufweichen kann,
   - keine Secrets im Vault-Repository (Actions-Secrets leer), die Vorlage enthält Dependabot für die Aktionen,
   - Squash-Merge ist erlaubt und üblich (Punkt 4 trägt dafür).

   **Kosten und Voraussetzungen, die nicht im Code liegen** (zu prüfen gegen die aktuellen GitHub-Bedingungen, bevor der Vault angelegt wird): Geschützte Branches, Rulesets und erforderliche Reviews gibt es bei **privaten** Repositories nur in bezahlten Tarifen (nicht im kostenlosen Tarif). CODEOWNERS mit Teams setzt eine Organisation voraus; Besitzer brauchen Schreibrechte. Secret Scanning mit Push Protection für private Repositories ist ebenfalls tarifabhängig. Ohne diese Voraussetzungen gibt es kein erzwungenes Vier-Augen-Prinzip, und die App darf es nicht so darstellen (Punkt 10).
10. **Prüfung statt Vertrauen.** Die Einstellungen lassen sich nicht im Repository ablegen. Deshalb liefert die Vorlage:
    - eine **importierbare Ruleset-Datei** (`.github/ruleset.json`, in den Einstellungen importierbar),
    - einen **lesenden** Befehl `chronicle vault doctor`, der über das `gh` des Benutzers (`gh api`, mit dessen eigener Anmeldung, **ohne gespeichertes Token**) den Schutz des Standard-Branches abfragt und fehlende Punkte meldet,
    - die Anzeige in der Oberfläche: „Schutz des Hosters nicht bestätigt“, solange `doctor` nicht grün war. „Freigegeben“ in der Oberfläche heißt nur „im Standard-Branch“, nicht „von einer zweiten Person geprüft“.
11. **Notfall und Bypass, in sich widerspruchsfrei.** Drei Fälle mit einer **dokumentierten Bypass-Liste** (Datei `docs/bypass.md` im Vault, benennt Personen oder Team):
    - *Normalbetrieb:* niemand darf umgehen, auch Administratoren nicht („eingeschlossen“).
    - *Notfall-Freigabe:* die benannte Bypass-Gruppe darf einen PR selbst freigeben und zusammenführen. Jeder Gebrauch wird nachträglich im Protokoll des Hosters geprüft.
    - *Bereinigung der Historie (ADR 0021):* braucht Force-Push. Das ist kein Bypass, sondern ein **vorübergehendes Ausschalten des Regelwerks durch einen Organisationsinhaber**, das im Protokoll des Hosters steht und danach wieder eingeschaltet wird.

### Vorfälle: Live-Dokumentation (entschieden: kein Live-Modus)

12. **Problem.** Mit einem Pull Request je Person ist die Live-Dokumentation bei einem Vorfall nicht erreichbar: Jede Person sieht nur ihren eigenen Stand, bis die Freigabe da ist.
13. **Entscheidung des Projektinhabers (2026-10-27): Es gibt keinen Incident-Branch und keinen Live-Modus.** Im Vorfall dokumentiert in der Regel **eine** Person. Jede andere Person kann Informationen teilen, die dokumentierende Person trägt sie ein. Wer mehrere Dokumentierende braucht, einen Sammel-Pull-Request oder einen anderen Ablauf, **stimmt das außerhalb des Werkzeugs ab**. Chronicle bildet es nicht ab.
14. **Folgen.** Punkte 1 bis 8 und 15 gelten unverändert: ein Arbeits-Branch je Klon, ein Pull Request, Freigabe durch den Hoster. Ein Weg für geteiltes Schreiben auf einem gemeinsamen Branch entsteht nicht, ebenso keine Kennzeichnung „ohne Freigabe“ für einen Incident-Branch. **Verworfen:** der Vorschlag, für ein Topic vom Typ `incident` einen geteilten Branch `incident/<topic-key>` ohne Review einzurichten, mit Live-Modus (Speichern pusht sofort, zyklisches Aktualisieren) und einem Sammel-Pull-Request nach dem Vorfall. Er hätte das Vier-Augen-Prinzip für die Dauer eines Vorfalls geschwächt. Wer ihn später braucht, kann ihn als neue Entscheidung vorschlagen.

15. **Berichte und Exporte werden standardmäßig aus dem freigegebenen Stand (`origin/<standard>`) gebaut.** Enthält die Auswahl ungeprüfte Events (Arbeitsstand), muss der Aufrufer das ausdrücklich verlangen, und der Bericht kennzeichnet sie. Das verhindert, dass ein Bericht an Dritte ungeprüfte Inhalte als geprüft ausgibt.

### Einzelpersonen

16. Für einen Vault mit einer einzigen Person gilt: **Pull Request mit 0 erforderlichen Freigaben und erforderlicher CI** (der Standardfall der Vorlage für diese Gruppe). Der Direkt-Modus (`workflow = "direct"` in der Registry, nicht im Manifest) bleibt eine Ausnahme für Tests und Entwicklung. Er wird in der Oberfläche als „ohne Freigabe“ gekennzeichnet. Der Modus steht nicht im Manifest, weil ein Vault ihn sonst allen Klonen vorschreiben könnte. Dasselbe Prinzip gilt allgemein: **Alles, was das Verhalten der App auf dem Arbeitsplatz festlegt (Workflow, Limits über die Obergrenzen hinaus, Speicherart der Anhänge), kommt aus der lokalen Registry oder den Obergrenzen der App, nicht aus dem Vault** (ADR 0013, Punkt 6).

## Konsequenzen

- Freigabe hängt am Hoster, nicht an der App (ADR 0015). Das ist der einzige Weg, der nicht umgangen werden kann, indem jemand die Dateien direkt ändert.
- Der Link-Ansatz kostet einen Klick, spart aber ein Geheimnis.
- Nach dem Merge auf dem Hoster sehen andere den neuen Stand erst nach „Aktualisieren“.
- Aufwand: `sync.synchronise` wird umgebaut (Teile 14a und 14b), die Oberfläche bekommt den Status (Teile 15a und 15b), der Zustandsautomat braucht eigene Tests.
- Teil 14a (Commit je Speichern auf Arbeits-Branch) wird nicht ohne Teil 14b (Einreichen) ausgeliefert: Bis dahin bleibt `workflow = "direct"` oder ein Funktionsschalter aus.
- Die Annahmen zu GitHub (kein eigener Merge-Treiber auf dem Server, Verhalten von Squash und Signaturen, Sichtbarkeit von `refs/pull/*`) werden vor Teil 14b in einem Testrepository geprüft (Plan Teil 12a).

## Offene Punkte

- Anzahl der Freigaben als Vorgabe der Vorlage (1 oder 2).
- ~~Entscheidung zu Punkt 12 bis 14 (Incident-Branch).~~ Entschieden: kein Live-Modus.
- Hoster-Tarif (Punkt 9), Entscheidung des Betreibers.
