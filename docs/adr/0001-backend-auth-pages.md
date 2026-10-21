# ADR 0001: Server mit Anmeldung, Benutzerverwaltung und Seiten

- **Status:** Akzeptiert (Richtung bestätigt, Details siehe unten)
- **Datum:** 2026-10-21
- **Bezug:** #9 (Bestandsaufnahme), #11 (Seiten statt Tabs), #12 (Menü), #13 (Anmeldung)

## Kontext

Das Dashboard ist heute eine statische Seite, die aus einer YAML-Datei gebaut wird. Drei Anforderungen passen nicht dazu:

1. **Zugriffsschutz.** Eine Login-Maske im Browser schützt nichts, weil die Daten im HTML liegen. Der Schutz muss vor der Auslieferung sitzen. Eine statische Datei kann man nicht mit Benutzerrechten schützen, deshalb muss ein Server die Inhalte ausliefern.
2. **Interaktive Änderungen.** Events, Evidence und Akteure sollen über API und GUI hinzugefügt werden können, ohne den Build neu anzustoßen.
3. **Benutzerverwaltung und Seiten.** Mehrere Benutzer mit Rollen, dazu eigene Seiten für Übersicht, Timeline, Graph, Events, Evidence, Einstellungen und Benutzer.

## Entscheidung

**Ein Python-Server (FastAPI) rendert die Seiten serverseitig und hält die Daten in SQLite.**

- **„Statisch“ heißt hier:** keine SPA und kein Frontend-Framework. Die Seiten werden serverseitig aus Jinja2-Templates gerendert, die Inhalte kommen aus der Datenbank. CSS, JavaScript und Icons sind statische Dateien, die der Server ausliefert. Tailwind wird wie bisher lokal gebaut.
- **Export für Berichte.** Zusätzlich gibt es einen eigenständigen HTML-Export eines Incidents (ohne Server, ohne Login), etwa als Anhang zum Post-Mortem. Der Export ist eine eigene Funktion und kein Ersatz für die Live-Ansicht.
- **Speicher.** SQLite in einem Docker-Volume. Die YAML bleibt als **Import- und Exportformat** erhalten, das bestehende Pydantic-Schema wird weiterverwendet.
- **Anmeldung.** Benutzername und Passwort. Passwörter mit Argon2id gehasht. Sitzungen als `HttpOnly`-, `SameSite=Strict`-Cookies, serverseitig mit Ablaufzeit. CSRF-Schutz für alle schreibenden Requests. Login-Versuche gedrosselt.
- **Benutzerverwaltung.** Rollen `admin`, `analyst`, `viewer`. Admins legen Benutzer an, setzen Rollen und Passwörter zurück und deaktivieren Konten. Der Bootstrap-Admin kommt aus Umgebungsvariablen beim ersten Start, nie aus dem Repo.
- **API.** REST unter `/api/v1` mit denselben Rechten wie die GUI. Token-Zugriff für Automatisierung kommt später, zunächst nur Session.
- **Seiten.** Übersicht, Timeline, Graph, Events, Evidence, Einstellungen, Benutzer. Ansichten über eigene URLs statt Hash-Router, weil sie serverseitig gerendert werden.
- **Theme.** Hell und dunkel, pro Benutzer gespeichert. Die Einstellung wird im Profil abgelegt, nicht im `localStorage`.
- **Audit.** Jede schreibende Aktion und jeder Login wird mit Benutzer, Zeit und Ziel protokolliert.

## Konsequenzen

- Der Dienst braucht eine laufende Instanz. Das ist der Preis für Zugriffsschutz, Rollen und Live-Bearbeitung. Statische Dateien bleiben nur für CSS, JavaScript und den Berichts-Export.
- Evidence-Dateien werden nur noch über einen autorisierten Endpunkt ausgeliefert, nicht mehr als öffentliche Dateien.
- Uploads brauchen Grenzen: Dateigröße, erlaubte Typen per Magic-Bytes prüfen, Dateinamen neu vergeben, nichts unter dem Original-Namen speichern.
- Mehr Testaufwand: API- und Auth-Tests, dazu die bisherigen Browser-Tests.
- Die Python-Abhängigkeiten wachsen (FastAPI, Uvicorn, argon2-cffi, Jinja2 bleibt). Dependabot deckt das bereits ab.

## Verworfene Alternativen

- **Statische Seite mit Login im Browser.** Schützt nichts, siehe Kontext.
- **Statische Seite plus Build-Trigger bei Änderungen.** Die Seite bliebe ungeschützt, und Benutzer hätten keine Rollen.
- **SPA (React/Vue) mit eigenem Backend.** Mehr Build-Kette und zweite Sprache, ohne dass die Seiten davon profitieren.
- **Identity Provider (OIDC) von Anfang an.** Sinnvoll für Unternehmen, aber für den Start zu schwergewichtig. Der Umstieg bleibt möglich, weil die Sitzungslogik gekapselt wird.

## Offene Punkte

- Passwort-Richtlinie (Länge, Sperrfrist nach Fehlversuchen)
- Ob Mehrmandantenfähigkeit nötig ist (aktuell: ein Incident pro Instanz)
- Aufbewahrung und Löschung von Evidence
