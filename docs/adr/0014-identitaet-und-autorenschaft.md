# ADR 0014: Identität und Autorenschaft, Git-Autor und App-Benutzer

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-10-25
- **Bezug:** ADR 0001 (Rollen, Audit), ADR 0013, ADR 0015, `chronicle/accounts.py`

## Kontext

Im Vault zählt, wer eine Änderung gemacht hat und wer sie freigegeben hat. Es gibt drei Quellen für „wer“:

1. den **App-Benutzer** (Konto in `ops.db`, lokal, vom Admin der Installation angelegt),
2. den **Git-Autor** eines Commits (Name und E-Mail, frei wählbar, nicht geprüft),
3. das **Konto beim Hoster** (GitHub), das pusht und Pull Requests freigibt (durch den Hoster authentifiziert).

Nur Nummer 3 ist gegenüber dem Team belastbar. Nummer 1 und 2 sind Angaben des Benutzers selbst.

## Entscheidung

1. **Die Berechtigung kommt vom Hoster.** Wer in den Vault schreiben und freigeben darf, entscheidet das Repository auf GitHub (Zugriffsrechte, Branch Protection, CODEOWNERS; ADR 0015). Die Rollen der App (ADR 0013) sind eine Hilfe für die Oberfläche und kein Schutz. Wer Schreibzugriff auf den Klon hat, kann die Dateien ohne App ändern. Die Freigabe hängt deshalb nie an der App.
2. **Der Commit-Autor ist die Person, die in der App eingeloggt ist.** `ops.users` bekommt die Felder `git_name` und `git_email`. `chronicle init` und das Anlegen eines Kontos belegen `git_name` aus `git config user.name` vor. **`git_email` wird nicht aus der privaten Adresse vorbelegt:** Vorgabe ist die Pseudonym-Adresse des Hosters (`<id>+<name>@users.noreply.github.com`), die der Benutzer eingibt. GitHub lehnt Pushes ab, die eine private Adresse preisgeben würden, wenn der Benutzer „E-Mail-Adresse privat halten“ eingestellt hat (Fehler GH007). Das löst den Widerspruch zwischen diesem ADR und ADR 0020, Punkt 15. Jeder Commit der Anwendung setzt Autor und Committer ausdrücklich (`GIT_AUTHOR_*`, `GIT_COMMITTER_*` in der Umgebung des Aufrufs, ADR 0021) und ändert nie die Git-Konfiguration des Benutzers.
3. **Ein Trailer macht die App-Herkunft sichtbar.** Jede Commit-Nachricht endet mit `Chronicle-User: <benutzername>`. Der Trailer ist ein Hinweis, kein Beweis.
4. **Authentizität kommt durch Signaturen, wenn gewünscht.** Die Anwendung signiert nicht selbst und kennt keine Schlüssel. Hat der Benutzer in Git `commit.gpgsign` oder `gpg.format=ssh` mit `user.signingkey` eingerichtet, nutzt der Commit-Aufruf diese Einstellung (das Signieren läuft in Git und im SSH-Agent). **Einschränkung (Review):** Bei Squash-Merge ersetzt der Hoster die Commits durch einen neuen, vom Hoster signierten. Die Signatur der Person geht verloren, die Pflicht „signierte Commits“ bringt dann nur noch Wert auf dem Arbeits-Branch, nicht im Standard-Branch. Sie hat nur Sinn, wenn der Vault mit Merge-Commit oder Rebase-Merge arbeitet. Deshalb ist Signieren **eine Empfehlung, keine Pflicht der Vorlage** (offene Frage 2, Vorgabe: Empfehlung).
5. **Wer freigibt, ist der GitHub-Account des Prüfers** (Review-Eintrag im Pull Request). Die App speichert keine Freigabe und stellt sie nur dar (ADR 0015).
6. **Das Audit-Log der App bleibt lokal und ergänzend.** Es wird nicht als Nachweis für Inhalte behandelt. Der Nachweis für Inhalte ist die Git-Historie plus der Review-Verlauf des Pull Requests. Das lokale Log deckt Anmeldungen, Kontenänderungen, Fehlversuche und Exporte ab. Eine optionale Hash-Kette, die an Commits verankert wird, macht Veränderungen des Logs erkennbar (ADR 0013, Punkt 5).
7. **Eigenfreigabe verhindert der Hoster.** GitHub lässt den Autor eines Pull Requests nicht selbst freigeben. Für Vaults mit einer einzigen Person geht das nicht. Dort gilt die Branch Protection mit 0 Freigaben als bewusste Einstellung, nicht als Fehler (offene Frage 3).

## Betrieb für mehrere Personen auf einem Server (Server-Modus, später)

Läuft eine Instanz für mehrere Personen mit einem Klon, pusht ein einziges Konto (Deploy Key oder Bot). Dann ist der Pull-Request-Autor das Bot-Konto, die Prüfer sind Menschen. Der echte Autor steht nur im Commit-Autor und im Trailer, beides Eigenangaben der App. Der Server-Modus ist im ersten Ausbau **nicht vorgesehen**. Wird er gebaut, braucht er: Schreibrechte des Bot-Kontos nur auf Branches `chronicle/**`, Pflicht-Freigabe durch einen Menschen außer dem Bot, und im Pull Request die Namen der Autoren als Text.

## Konsequenzen

- Ändert jemand `git_email` in der App auf eine fremde Adresse, wirkt der Commit, als käme er von dort. Die Gegenmaßnahme sind Signaturen und die Prüfung im Pull Request, nicht die App.
- `must_change_password` und die Rollenlogik bleiben. Das Löschen oder Deaktivieren eines Benutzers ändert nichts an früheren Commits.
- Ein Benutzer ohne `git_email` darf in der App lesen und nicht schreiben (die Oberfläche verlangt Name und E-Mail vor dem ersten Speichern).
- Datenschutz: E-Mail-Adressen in der Git-Historie sind personenbezogen und bleiben dort. Empfohlen sind GitHub-`noreply`-Adressen (ADR 0020).

## Offene Punkte

- Siehe `vault-git-plan.md`, offene Fragen 2 und 3.
- Der Arbeits-Branch gehört dem Klon, nicht dem Benutzer (ADR 0015, Punkt 1). Wechselt der angemeldete Benutzer, tragen neue Commits seinen Namen. Höchstens eine Schreibsitzung je Klon.
