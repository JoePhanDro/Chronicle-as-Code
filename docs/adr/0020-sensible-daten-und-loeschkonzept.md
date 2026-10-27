# ADR 0020: Sensible Daten in Git, Geheimnisse und Löschkonzept

- **Status:** Vorgeschlagen (zu bestätigen). Wird **nicht** „Angenommen“, bevor der Bereinigungsablauf (Punkt 10) an einem Testrepository durchgespielt wurde (Plan Teil 16b). Rechtliche Punkte (Datenschutz) sind **keine Rechtsberatung** und müssen mit der oder dem Datenschutzbeauftragten geklärt werden.
- **Datum:** 2026-10-25
- **Bezug:** ADR 0008 (Konsequenzen: Bereinigung der Historie), ADR 0014, ADR 0019, Issue #98, `CONTRIBUTING.md` (Sicherheit)

## Kontext

Git-Historie ist unveränderlich in dem Sinn, dass jeder Klon, jeder Fork und jeder Pull-Request-Verlauf eine Kopie trägt. Ein Vault enthält Vorfallsdaten: Systemnamen, Zeitpunkte, Vorgehen von Angreifern, Screenshots und Logs, dazu Namen von Beteiligten. Daraus folgen zwei Risiken:

1. **Geheimnisse** (Passwörter, Tokens, Schlüssel, Sitzungs-IDs) landen in Logs und Screenshots.
2. **Personenbezogene Daten** (Namen, E-Mail-Adressen, IP-Adressen, Benutzerkennungen) unterliegen Löschpflichten und Aufbewahrungsfristen, die mit einer unveränderlichen Historie schwer vereinbar sind.

## Entscheidung

### Was nie in den Vault gehört

1. **Geheimnisse nie.** Kein Passwort, kein API-Token, kein privater Schlüssel, keine Sitzungs-ID, kein Wiederherstellungscode. Auch nicht in Anhängen und nicht im Klartext von Logs. Die Anwendung selbst legt im Vault keine Geheimnisse ab: Konten, Hashes, Sitzungen und Konfiguration liegen im Zustandsordner (ADR 0013). Die Vorlage verbietet in `.gitignore` `*.db`, `.env`, `*.pem`, `*.key`.
2. **Personenbezogene Daten nur im nötigen Umfang.** Regel für Autoren: Rollen und Funktionen statt Namen (`actor: Administrator Webserver`), sofern der Name nicht für den Vorfall zwingend ist. Das ist eine Empfehlung der Vorlage (README, Pull-Request-Vorlage), die Anwendung kann sie nicht erzwingen.

### Vorbeugung (technisch)

3. **Prüfung auf Geheimnisse:** `chronicle check --secrets` sucht in allen Textfeldern, Textanhängen (`.txt`, `.log`) und Dateinamen nach bekannten Mustern (AWS-Schlüssel, private Schlüsselblöcke, GitHub-Tokens, JWT, `password=`-Zuweisungen). Treffer sind Fehler, ein Eintrag in einer Allowlist-Datei im Vault (`.chronicle-secrets-allow`, Hash des Treffers, mit Begründung) hebt einen Fehlalarm auf. Die Prüfung läuft lokal vor dem Commit (die App prüft vor jedem Speichern) und in der Vault-CI.
4. **Hoster-Funktionen** aktivieren, soweit der Tarif sie bietet: Secret Scanning mit Push Protection.
5. **Bilder** lassen sich nicht automatisch prüfen. Die Pull-Request-Vorlage fragt ausdrücklich nach geschwärzten Screenshots. Prüfer sehen das Bild im PR.
6. **Der Vault ist privat.** Der Hoster-Zugriff ist auf Personen mit Bedarf beschränkt, Forks sind deaktiviert, die Zwei-Faktor-Anmeldung ist für die Organisation Pflicht.

### Löschen im Alltag

7. **Löschen eines Events oder Anhangs ist ein Commit.** Die Datei verschwindet aus dem Arbeitsstand, die Historie behält sie. Das ist gewollt: Nachvollziehbarkeit, was wann bekannt war (ADR 0008, Punkt 4).

### Bereinigung (Ausnahmeverfahren)

Gilt, wenn ein **Geheimnis** oder **Daten, die nicht hätten gespeichert werden dürfen**, im Vault gelandet sind (Issue #98):

8. **Zuerst rotieren, dann bereinigen.** Ein Geheimnis gilt ab dem Commit als offengelegt. Die Rotation (neues Passwort, widerrufener Token) ist die eigentliche Gegenmaßnahme. Die Bereinigung der Historie verringert nur die weitere Verbreitung.
9. **Entscheidung** trifft die Vault-Verantwortliche oder der Vault-Verantwortliche; bei personenbezogenen Daten gemeinsam mit der oder dem Datenschutzbeauftragten. Die Entscheidung wird (ohne die Daten) im Vault-Verzeichnis `docs/bereinigungen.md` festgehalten, Datum und Grund, damit die Umschreibung nachvollziehbar bleibt.
10. **Ablauf** (technisch, als Skript `scripts/vault-purge.sh` mit Trockenlauf, im Testrepository durchgespielt):
    1. Alle Beteiligten stoppen Pushes. Pull Requests werden geschlossen, offene Arbeits-Branches gesichert.
    2. Spiegel-Klon (`git clone --mirror`) und `git filter-repo --path <pfad> --invert-paths` oder `--replace-text` auf dem Spiegel.
    3. Das Regelwerk des Hosters wird durch einen Organisationsinhaber **vorübergehend ausgeschaltet** (kein Bypass, siehe ADR 0015, Punkt 11), dann `git push --force` der bereinigten Branches und Tags, danach wird das Regelwerk wieder eingeschaltet. **Achtung:** `git push --mirror` überträgt auch Refs, die der Hoster nicht annimmt oder ausblendet (`refs/pull/*` sind schreibgeschützt), und kann scheitern. Ein Push je Branch und Tag ist verlässlicher. `git filter-repo` entfernt außerdem den Remote `origin` aus der Konfiguration des Spiegels; er muss danach neu gesetzt werden.
    4. **Pull-Request-Referenzen** (`refs/pull/*`) und Hoster-Caches lassen sich nicht per Push entfernen. Der Hoster-Support wird um das Entfernen zwischengespeicherter Sichten und der Referenzen gebeten. Ist das nicht möglich oder nicht schnell genug, bleibt als Ausweg ein **neues Repository** aus der bereinigten Historie; der alte Vault wird gelöscht.
    5. **Alle Klone werden neu geklont**, nicht aktualisiert (ein `pull` bringt die alten Objekte zurück). Die Indexe (`cache/`) werden gelöscht. Forks und Backups werden bereinigt oder vernichtet.
    6. Die Prüfung `git log --all -S<muster>` auf dem frischen Klon bestätigt das Ergebnis.
11. **Grenzen sagen wir offen:** Wer vor der Bereinigung geklont hat, besitzt die Daten. Die Bereinigung beseitigt sie nicht aus Kopien, die Dritte angelegt haben.

### Löschkonzept und Aufbewahrung (Datenschutz)

12. **Aufbewahrungsfrist je Vault** legt der Betreiber fest und trägt sie in `README` des Vaults ein. Für abgelaufene Topics gibt es zwei Wege: Löschen im Arbeitsstand (Punkt 7) mit anschließender Bereinigung nach Punkt 10, wenn die Frist das verlangt, oder Verzicht auf personenbezogene Inhalte von Anfang an (Punkt 2). **Empfohlen ist der zweite Weg**, weil Punkt 10 aufwendig ist und Kopien nicht sicher erfasst.
13. **Auskunft und Löschung** auf Verlangen betroffener Personen: Die Suche der Anwendung findet Inhalte im Arbeitsstand, die Git-Suche (`git log -S`) in der Historie. Das Löschen folgt Punkt 10.
    **Spannung zwischen Löschpflicht und Dokumentationspflicht** (zur Klärung mit der oder dem Datenschutzbeauftragten, keine Rechtsberatung): Das Recht auf Löschung (Art. 17 DSGVO) steht gegen die Pflicht, Datenschutzverletzungen zu dokumentieren (Art. 33 Abs. 5 DSGVO), und gegen berechtigte Aufbewahrungszwecke. Ein Sicherheitsvorfall-Vault kann genau diese Dokumentation sein. Die Vorgabe ist deshalb Datenminimierung (Punkt 2) und eine Einzelfallentscheidung, kein automatisches Löschen.
14. **Hoster und Auftragsverarbeitung.** Mit GitHub (oder einem anderen Hoster) liegen Vorfallsdaten bei einem Dritten, gegebenenfalls außerhalb der EU. Ob ein Vertrag zur Auftragsverarbeitung besteht und welcher Standort gilt (z. B. GitHub Enterprise Cloud mit Datenresidenz in der EU), entscheidet der Betreiber, nicht das Projekt (Entscheidung des Projektinhabers, 2026-10-27: GitHub ist nur der Vorfallsspeicher, in eigenen Vorfall-Repositories). GitHub ist der Standard-Hoster (ADR 0011, Punkt 6). Der Kern der Anwendung bleibt hoster-neutral; ein selbst betriebener Git-Server (Gitea, GitLab) ist „unterstützt, nicht Standard“.
15. **E-Mail-Adressen von Autoren.** Commits tragen Namen und Adresse (ADR 0014). Vorgabe sind Pseudonym-Adressen des Hosters (`…@users.noreply.github.com`). Die App belegt `git_email` **nie** aus einer privaten Adresse vor (ADR 0014, Punkt 2).

## Konsequenzen

- Die Entscheidung, Anhänge in den Vault zu legen (ADR 0019), macht Punkt 3 bis 6 zu dem, was Betreiber vor dem ersten Einsatz mit echten Daten umsetzen sollten. Die Betriebsanleitung beschreibt es, `chronicle vault doctor` prüft, was sich prüfen lässt. Die Anwendung erzwingt es nicht.
- Der Ablauf in Punkt 10 braucht administrative Rechte auf dem Hoster und kostet Betriebszeit. Er ist die Ausnahme.
- `CONTRIBUTING.md` verbietet echte Evidence im Code-Repository. Das gilt weiter. Echte Daten gehören ausschließlich in Vaults.

## Offene Punkte

- ~~Hoster, Region, Vertrag zur Auftragsverarbeitung.~~ Entschieden: Sache des Betreibers.
- Konkrete Aufbewahrungsfristen.
