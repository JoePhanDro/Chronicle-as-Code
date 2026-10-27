# Betrieb mit HTTPS

`docker compose up -d` startet Chronicle immer hinter einem TLS-Proxy (Caddy). Der Server selbst ist von außen nicht erreichbar. Der Proxy bedient die App nur auf Port 443, Port 80 leitet auf HTTPS um. Die Konfiguration liegt im Repository und wird in der CI getestet (`scripts/check_proxy.sh`).

## Aufbau

```
Browser ──HTTPS 443──▶ proxy (Caddy, feste Adresse) ──HTTP──▶ api (8000, nicht veröffentlicht)
```

- Caddy beendet TLS. Das Zertifikat kommt aus einer von drei Quellen (siehe unten).
- Die API ist nur im Compose-Netz erreichbar.
- Die API vertraut `X-Forwarded-For` nur von der festen Adresse des Proxys (`CHRONICLE_TRUSTED_PROXIES`). So zählen Fehlversuche und das Audit-Log die Adresse des Clients und nicht die des Proxys ([Issue #27](https://github.com/JoePhanDro/Chronicle-as-Code/issues/27)).
- Das Sitzungscookie ist immer `Secure` (`CHRONICLE_COOKIE_SECURE=1`).

## Start

```bash
cp .env.example .env
# In .env setzen: CHRONICLE_ADMIN_USER, CHRONICLE_ADMIN_PASSWORD und, für eine Domain, CHRONICLE_HOSTNAME

docker compose --profile import run --rm importer
docker compose up -d
```

Danach `https://<CHRONICLE_HOSTNAME>/login` öffnen (Standard: `https://localhost/login`). Voraussetzung ist Docker mit Compose ab Version 2.24.

## Das Zertifikat

Der Proxy wählt beim Start, woher das Zertifikat kommt. Im Log steht die Wahl (`docker compose logs proxy`).

| `CHRONICLE_TLS` | Wirkung |
|---|---|
| leer (Standard) | Liegen `certs/cert.pem` und `certs/key.pem` vor, werden sie benutzt, sonst erzeugt der Proxy ein eigenes Zertifikat. |
| `files` | Nur die Dateien in `certs/`. Fehlt eine, startet der Proxy nicht. |
| `internal` | Immer ein eigenes Zertifikat aus Caddys lokaler CA, auch wenn Dateien vorliegen. |
| `acme` | Ein echtes Zertifikat von Let's Encrypt für eine öffentliche Domain. |

**Eigene Dateien:** `cert.pem` (mit Zwischenzertifikaten) und `key.pem` in den Ordner `certs/` legen und den Proxy neu starten (`docker compose up -d --force-recreate proxy`). Der Ordner wird nicht versioniert, ein privater Schlüssel gehört nicht ins Repository. Ein anderer Ordner lässt sich mit `CHRONICLE_CERTS_DIR` angeben. Die Dateirechte können restriktiv sein (z. B. `600` für den Schlüssel): Der Proxy bekommt dafür die Capability `DAC_READ_SEARCH`, die nur Lesen und Durchsuchen erlaubt.

**Eigenes Zertifikat des Proxys:** Das ist das Verhalten ohne Dateien. Das Zertifikat ist selbst signiert. Der Browser warnt, bis das Root-Zertifikat von Caddy installiert ist. Es liegt im Volume `caddy-data` (`pki/authorities/local/root.crt`). Für einen Test auf dem eigenen Rechner genügt die Warnung wegzuklicken.

**Echtes Zertifikat:** `CHRONICLE_HOSTNAME` auf die Domain setzen, `CHRONICLE_TLS=acme`. Die Domain muss auf den Server zeigen, und Port 80 und 443 müssen aus dem Netz erreichbar sein. Port 80 braucht Caddy für die Ausstellung. Caddy erneuert das Zertifikat selbst.

## Konfiguration

| Variable | Standard | Wirkung |
|---|---|---|
| `CHRONICLE_HOSTNAME` | `localhost` | Adresse, für die der Proxy antwortet. |
| `CHRONICLE_TLS` | leer | Zertifikatsquelle, siehe oben. |
| `CHRONICLE_CERTS_DIR` | `./certs` | Ordner mit `cert.pem` und `key.pem`. |
| `CHRONICLE_HTTPS_PORT`, `CHRONICLE_HTTP_PORT` | `443`, `80` | Host-Ports des Proxys. Nur ändern, wenn sie belegt sind. Die Umleitung von HTTP geht dann trotzdem auf Port 443. |
| `CHRONICLE_SUBNET`, `CHRONICLE_PROXY_IP` | `172.30.0.0/24`, `172.30.0.10` | Das Netz der Container und die feste Adresse des Proxys. Nur ändern, wenn das Subnetz schon anderweitig belegt ist (`invalid pool request: Pool overlaps`). Die Adresse muss im Subnetz liegen. |

## Entwicklung ohne HTTPS

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

Das startet nur den Server auf `http://127.0.0.1:8000`, ohne Proxy, mit einem Cookie ohne `Secure`. Nur auf dem eigenen Rechner benutzen. Das feste Subnetz entfällt dabei.

## Was im Audit-Log steht

Caddy hängt die Adresse des Clients an `X-Forwarded-For` an. Die API nimmt den rechtesten Eintrag, der kein vertrauenswürdiger Proxy ist. Ein vom Client mitgeschickter Header wird damit ignoriert.

Auf einem Testrechner mit Docker Desktop zeigt das Audit-Log das Gateway des Netzes (z. B. `172.30.0.1`), nicht den Client. Auf einem Linux-Server bleibt die Adresse des Clients normalerweise erhalten. Das lässt sich nach dem Start im Audit-Log prüfen.

## HSTS

Der Proxy setzt keinen `Strict-Transport-Security`-Header. HSTS gilt pro Hostname, ohne Port. Ein Browser würde danach auch `http://localhost:8000` nicht mehr annehmen. Für eine produktive Domain, die dauerhaft nur HTTPS nutzt, kann man den Header in `deploy/Caddyfile` ergänzen.

## Zertifikate des Proxys sichern

Caddy speichert seine CA und ausgestellte Zertifikate im Volume `caddy-data`. Dieses Volume nicht löschen. Bei `acme` fordert Caddy sonst neue Zertifikate an, und der Anbieter kann Grenzen setzen. Bei `internal` entsteht eine neue CA, und ein installiertes Root-Zertifikat passt nicht mehr.

## Prüfen

```bash
./scripts/check_proxy.sh
```

Das Skript startet den Stack mit einem leeren Datenbank-Volume und prüft:

- HTTPS antwortet mit dem Health-Check,
- ohne Dateien in `certs/` kommt das Zertifikat von Caddys CA, mit Dateien das eigene,
- HTTP wird auf HTTPS umgeleitet (308),
- der Port der API ist von außen nicht erreichbar,
- das Sitzungscookie trägt `Secure` und `HttpOnly`,
- ein gefälschter `X-Forwarded-For` landet nicht im Audit-Log,
- das Entwicklungs-Override startet den Server auf Port 8000 ohne Proxy.

Danach räumt es auf. Mit `KEEP=1 ./scripts/check_proxy.sh` bleibt der Stack laufen. Es braucht `openssl`. Sind die Ports 80/443 oder das Subnetz belegt, die Variablen aus der Tabelle setzen. Die CI führt dasselbe Skript bei jedem Pull Request aus.

## Grenzen

- Der Proxy schützt nicht gegen Überlastung und ist keine Web Application Firewall. Die Begrenzung der Fehlversuche übernimmt die API.
- Es gibt nur einen Hostnamen.
- Ein selbst erzeugtes Zertifikat bleibt für Browser unbekannt, bis das Root-Zertifikat installiert ist. Das ist kein Fehler, sondern der Unterschied zu einem Zertifikat einer bekannten CA.
- Das Entwicklungs-Override veröffentlicht den Server auf `127.0.0.1:8000` am Proxy vorbei. Nicht im Netz einsetzen.
- Backup: das Volume `chronicle-db` wie im Abschnitt [Export und Backup](../README.md#export-und-backup) sichern. Die Zertifikate im Volume `caddy-data` sind nicht Teil der Daten.
