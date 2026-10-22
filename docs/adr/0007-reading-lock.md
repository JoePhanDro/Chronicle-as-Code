# ADR 0007: Lesen mit Sperre statt Anmeldung zum Lesen

- **Status:** Angenommen
- **Bezug:** Issue #13 (Zugriffsschutz), ADR 0001 (Rollen, Audit), ADR 0004 (Evidence)

## Kontext

Die Daten gelten nicht als geheim. Trotzdem sollen Unbefugte im Büro nicht ohne Weiteres die Ereignisse sehen. Die Anmeldung mit Konto gilt heute für alles, auch fürs Lesen. Das macht den Zugang zum Lesen aufwendig, etwa für Kollegen, die nur nachschauen wollen.

Eine Sperre im Browser (Overlay über der Seite) ist kein Schutz. Die Daten stehen dann im HTML und in den Entwicklerwerkzeugen.

## Entscheidung

**Lesen ohne Konto ist möglich, aber nur nach Eingabe eines Sperr-Passworts. Der Server hält die Daten zurück, bis das Passwort stimmt.**

- **Sperre einschalten:** Ein Admin setzt im Dialog „Einstellungen“ ein Sperr-Passwort (mindestens 12 Zeichen). Ohne Sperre bleibt alles wie bisher: Lesen nur mit Konto.
- **Entsperren:** Auf der Anmeldeseite gibt es ein zweites Formular „Ansicht entsperren“. Richtiges Passwort: Der Browser bekommt eine Sitzung für das Gastkonto `lesezugriff` mit der Rolle `viewer`. Alle bestehenden Prüfungen gelten dann.
- **Bearbeiten bleibt Konten vorbehalten:** Das Gastkonto hat die Rolle `viewer` und kann keine Änderung speichern. Es hat kein Passwort, mit dem man sich direkt anmelden könnte.
- **Drosselung:** Falsche Sperr-Passwörter zählen wie Fehlanmeldungen, pro Adresse. Nach 5 Fehlversuchen gibt es 429.
- **Passwort ändern oder Sperre aufheben:** Alle Entsperrungen enden sofort. Beim Aufheben wird das Gastkonto deaktiviert.
- **Audit:** Setzen, Aufheben, Entsperren und Fehlversuche landen im Audit-Log, ohne das Passwort.

## Verworfen

- **Sichtschutz im Browser:** verdeckt nur, die Daten bleiben im Quelltext.
- **Anmeldung ganz entfernen:** Konten, Rollen und Audit-Log würden wegfallen. Das ist für Bearbeitung nicht gewollt.

## Konsequenzen

- Das Gastkonto `lesezugriff` erscheint in der Benutzerliste. Man sieht es dort, kann es aber nicht zum Anmelden verwenden. Wer die Sperre aufhebt, deaktiviert es.
- Wer das Sperr-Passwort kennt, liest alles. Es ist ein gemeinsames Passwort. Ein Wechsel betrifft alle, die es nicht mehr wissen.
- Die Sperre ersetzt keine Netzwerkgrenze. Der TLS-Proxy aus Issue #35 bleibt nötig, sobald man aus dem Netz zugreift.
- Der Berichts-Export (ADR 0006) bleibt ein eigener Weg: Die Datei enthält die Daten und ist nicht durch die Sperre geschützt.
