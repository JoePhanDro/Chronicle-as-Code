# Betrieb mit TLS hinter einem Reverse Proxy

Der Compose-Stack läuft standardmäßig nur auf `127.0.0.1` ohne TLS. Sobald andere Rechner darauf zugreifen sollen, muss die Verbindung über HTTPS laufen. Dieses Dokument beschreibt den Betrieb mit Caddy als TLS-Proxy. Die Konfiguration liegt im Repository und wird in der CI getestet (`scripts/check_proxy.sh`).

## Aufbau

```
Browser ──HTTPS 443──▶ proxy (Caddy, 172.30.0.10) ──HTTP──▶ api (8000, nicht veröffentlicht)
```

- Caddy beendet TLS und holt die Zertifikate.
- Die API ist nur im Compose-Netz erreichbar. Der Host-Port 8000 ist im Proxy-Betrieb nicht mehr veröffentlicht.
- Die API vertraut `X-Forwarded-For` nur von Caddy (`CHRONICLE_TRUSTED_PROXIES`). So zählen Fehlversuche und das Audit-Log die Adresse des Clients und nicht die des Proxys ([Issue #27](https://github.com/JoePhanDro/Chronicle-as-Code/issues/27)).

## Voraussetzungen

- Docker mit Compose ab Version 2.24 (der Override nutzt `!reset`).
- Für echtes HTTPS: ein Domainname, der auf den Server zeigt, und Port 80 und 443 aus dem Netz erreichbar. Port 80 braucht Caddy für die Zertifikatsausstellung und die Weiterleitung auf HTTPS.
- Ohne Domain (nur zum Ausprobieren): `localhost`. Caddy stellt dann ein Zertifikat aus einer eigenen CA aus, und der Browser zeigt eine Warnung.

## Start

```bash
cp .env.example .env
# In .env setzen: CHRONICLE_ADMIN_USER, CHRONICLE_ADMIN_PASSWORD und CHRONICLE_HOSTNAME (z. B. chronicle.example.org)

docker compose -f docker-compose.yml -f docker-compose.proxy.yml --profile import run --rm importer
docker compose -f docker-compose.yml -f docker-compose.proxy.yml up -d
```

Danach `https://<CHRONICLE_HOSTNAME>/login` öffnen.

## Konfiguration

| Variable | Wert | Wirkung |
|---|---|---|
| `CHRONICLE_HOSTNAME` | Domainname | Adresse, für die Caddy Zertifikate holt. Standard: `localhost`. |
| `CHRONICLE_TRUSTED_PROXIES` | `172.30.0.10` (im Override gesetzt) | Nur dieser Peer darf `X-Forwarded-For` setzen. |
| `CHRONICLE_COOKIE_SECURE` | `1` (im Override gesetzt) | Das Sitzungscookie wird nur über HTTPS gesendet. Hinter einem TLS-Proxy Pflicht. |

Das Netz `172.30.0.0/24` und die feste Adresse des Proxys stehen in `docker-compose.proxy.yml`. Wer dieses Subnetz schon anderweitig nutzt, ändert beides dort gemeinsam (Subnetz, feste Adresse und `CHRONICLE_TRUSTED_PROXIES`). Der Caddy-Name und die Routen stehen in `deploy/Caddyfile`.

## Was im Audit-Log steht

Caddy hängt die Adresse des Clients an `X-Forwarded-For` an. Die API nimmt den rechtesten Eintrag, der kein vertrauenswürdiger Proxy ist. Ein vom Client mitgeschickter Header wird damit ignoriert.

Auf einem Testrechner mit Docker Desktop zeigt das Audit-Log das Gateway des Netzes (`172.30.0.1`), nicht den Client. Auf einem Linux-Server bleibt die Adresse des Clients normalerweise erhalten. Das lässt sich nach dem Start im Audit-Log prüfen.

## HSTS

Der Proxy setzt keinen `Strict-Transport-Security`-Header. HSTS gilt pro Hostname, ohne Port. Ein Browser würde danach auch `http://localhost:8000` nicht mehr annehmen. Für eine produktive Domain, die dauerhaft nur HTTPS nutzt, kann man den Header in `deploy/Caddyfile` ergänzen.

## Zertifikate

Caddy erneuert Zertifikate automatisch. Sie liegen im Volume `caddy-data`. Dieses Volume nicht löschen. Sonst fordert Caddy neue Zertifikate an, und der Anbieter kann Grenzen setzen.

## Prüfen

```bash
./scripts/check_proxy.sh
```

Das Skript startet den Stack mit einem leeren Datenbank-Volume und prüft:

- HTTPS antwortet mit dem Health-Check,
- HTTP wird auf HTTPS umgeleitet (308),
- der Port der API ist von außen nicht erreichbar,
- das Sitzungscookie trägt `Secure` und `HttpOnly`,
- ein gefälschter `X-Forwarded-For` landet nicht im Audit-Log.

Danach räumt es auf. Mit `KEEP=1 ./scripts/check_proxy.sh` bleibt der Stack laufen. Die CI führt dasselbe Skript bei jedem Pull Request aus.

## Grenzen

- Der Proxy schützt nicht gegen Überlastung und ist keine Web Application Firewall. Die Begrenzung der Fehlversuche übernimmt die API.
- Es gibt nur einen Hostnamen.
- Der Override entfernt die Veröffentlichung der API auf dem Host. Ohne Override ist der Port `127.0.0.1:8000` wieder offen und umgeht den Proxy. Beides nicht gleichzeitig für Zugriffe aus dem Netz betreiben.
- Backup: das Volume `chronicle-db` wie im Abschnitt [Export und Backup](../README.md#export-und-backup) sichern. Die Zertifikate im Volume `caddy-data` sind nicht Teil der Daten.
