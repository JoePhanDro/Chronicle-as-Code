# ADR 0015: Freigabe-Workflow mit Branch, Pull Request und Branch Protection

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-10-25
- **Bezug:** ADR 0008 Punkt 3 (Abgleichen), ADR 0012, ADR 0014, ADR 0016, `chronicle/sync.py`, `.github/CODEOWNERS`, Issues #90, #131
- **Ändert:** ADR 0008 Punkt 3: „Abgleichen“ schreibt nicht mehr auf den gemeinsamen Branch, sondern auf einen Arbeits-Branch.

## Kontext

Heute committet `sync.synchronise` Änderungen und pusht sie direkt auf den Branch, der ausgecheckt ist (`HEAD:refs/heads/<branch>`). Das ist für eine Person gedacht. Mehrere Personen sollen Änderungen gegenseitig freigeben. Gleichzeitig muss während eines Vorfalls schnell dokumentiert werden können, ohne dass jemand auf eine Freigabe wartet.

## Optionen

1. **Direkt auf `main` pushen** (heutiger Stand). Keine Freigabe.
2. **Pull Request je Änderung, Freigabe auf dem Hoster.** Bewährt, Review-Werkzeuge sind da, Historie bleibt nachvollziehbar.
3. **Eigener Freigabe-Status in den Dateien** (`approved_by:` im Event). Fälschbar, weil jede Person die Datei ändern kann. Verworfen.
4. **Eigene Freigabe-Oberfläche in der App.** Doppelt gebaut, vertrauenswürdig nur, wenn der Hoster sie erzwingt. Verworfen.

## Entscheidung

**Option 2.** Im Einzelnen:

1. **Die App committet nie auf den Standard-Branch.** Sie arbeitet auf einem **Arbeits-Branch** `chronicle/<benutzername>/<datum>-<kurzname>`. Es gibt je Benutzer und Vault zu einem Zeitpunkt einen offenen Arbeits-Branch („Arbeitsstand“). Fehlt er, legt die App ihn beim ersten Speichern von `origin/<standard-branch>` aus an.
2. **Jedes Speichern ist ein lokaler Commit** (Autor siehe ADR 0014). Das gibt Rückgängig-Möglichkeit und passt zu „alles oder nichts“ (ADR 0008). Die Nachricht nennt Aktion und Key, zum Beispiel `Event exploit-2026-10-14: Status geändert`.
3. **Die Oberfläche liest den Arbeitsstand**, also die ausgecheckte Arbeitskopie. Wer dokumentiert, sieht das Ergebnis sofort, ohne Freigabe abzuwarten. Ein Merkmal je Event zeigt **„freigegeben“** oder **„wartet auf Freigabe“**: Die Datei steht (mit gleichem Inhalt) auf `origin/<standard-branch>` oder nicht. Das berechnet `git diff --name-status origin/<standard-branch> HEAD`, ohne Netzzugriff nach einem `fetch`.
4. **„Zur Freigabe einreichen“ ersetzt „Abgleichen“.** Es prüft die Dateien (`check`), holt `origin` (ohne Sperre, ADR 0016, Issue #131), führt `origin/<standard-branch>` in den Arbeits-Branch ein, pusht **nur den Arbeits-Branch** (`push -u origin <branch>`, nie `--force`) und zeigt den Link zum Pull Request (`…/compare/<standard>...<branch>?expand=1`). Die App legt in der ersten Ausbaustufe **keinen Pull Request über die API** an und braucht damit **keinen Token** des Hosters. Eine Anbindung über `gh` ist später möglich (offene Frage 5).
5. **Aktualisieren:** „Aktualisieren“ holt `origin`, setzt den lokalen Standard-Branch per `merge --ff-only` auf den Stand des Remotes und führt ihn in den Arbeits-Branch ein. Nach einem Merge des Pull Requests auf dem Hoster beginnt der nächste Arbeitsstand von vorn. Der alte Branch wird lokal entfernt, wenn er im Standard-Branch enthalten ist. Auf dem Remote löscht GitHub ihn auf Wunsch (Einstellung „Automatically delete head branches“).
6. **Einstellungen auf dem Hoster** (der Vault-Administrator richtet sie ein; die App prüft sie nicht, ein Hinweis steht in der Vorlage). Pflicht:
   - Standard-Branch geschützt, Änderungen nur über Pull Request,
   - mindestens eine Freigabe, **Freigabe der Code-Owner** verlangt, veraltete Freigaben bei neuen Commits verwerfen, Freigabe des letzten Pushs durch eine andere Person,
   - der Status `Chronicle prüfen` (Workflow, ADR 0021) ist erforderlich,
   - Force-Push und Löschen des Branches verboten, Administratoren eingeschlossen.

   Empfohlen: Branch muss aktuell sein oder eine Merge Queue (verhindert, dass zwei einzeln grüne PRs zusammen einen Duplikat-Key ergeben, ADR 0012), signierte Commits (ADR 0014), Squash-Merge (ein Commit je Pull Request, Datei-Historie bleibt über `git log -- <datei>`).
7. **CODEOWNERS im Vault** (aus der Vorlage): `/vault.yaml`, `/.github/` und `/topics/*/topic.yaml` gehören dem Vault-Team; `/topics/<topic>/` kann einem Team je Topic zugeordnet werden; `/evidence/` gehört den Prüfern allgemein. Das ist unkritisch, weil Anhänge ohne Änderung der Event-Datei (Prüfsumme dort) nicht hinzukommen können und verwaiste Anhänge die Prüfung fehlschlagen lässt (ADR 0019). Eine Änderung an Event-Datei und Anhang wird damit immer vom Besitzer des Topics gesehen.
8. **Der Notfallweg.** Der Vault-Administrator darf einen Pull Request selbst freigeben und zusammenführen (Regel „Umgehen erlauben“ für eine kleine Gruppe, nicht für alle). Das Ereignis steht im Protokoll des Hosters. Für Vaults mit einer einzigen Person (kein Prüfer) ist die Freigabeanzahl 0 eine bewusste Einstellung.
9. **Direkt-Modus als Ausnahme.** Wer auf dem Standard-Branch arbeiten will (Einzelperson, privater Vault), setzt `workflow: direct` in der lokalen Registry (ADR 0013). Dann verhält sich die App wie heute. Der Modus steht nicht in `vault.yaml`, weil ein Vault ihn sonst allen Klonen vorschreiben könnte.

## Konsequenzen

- Freigabe hängt am Hoster, nicht an der App (ADR 0014). Das ist der einzige Weg, der nicht umgangen werden kann, indem jemand die Dateien direkt ändert.
- Der Link-Ansatz kostet einen Klick, spart aber ein Geheimnis (kein Token in der App).
- Nach dem Merge auf dem Hoster sehen andere den neuen Stand erst nach „Aktualisieren“. Das ist der Preis eines Offline-fähigen Modells (ADR 0008).
- Aufwand: `sync.synchronise` wird umgebaut (Branch-Verwaltung, kein Push auf den Standard-Branch), die Oberfläche bekommt den Freigabe-Status.
- Zwei Arbeits-Branches auf demselben Klon (zwei Benutzer der gleichen Installation) sind nicht vorgesehen. Die App hält einen Arbeits-Branch je Benutzer-Name im Klon und wechselt beim Benutzerwechsel nicht stillschweigend (Plan Teil 13 beschreibt das; Risiko).

## Offene Punkte

- Anzahl der Freigaben als Vorgabe der Vorlage (1 oder 2).
- Zeit bis zur Freigabe für Vorfälle: Nutzer entscheiden, ob der Notfallweg genügt.
