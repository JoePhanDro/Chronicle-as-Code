# Mitgelieferte Bibliotheken

Diese Dateien sind unverändert aus den npm-Paketen übernommen und werden von der App unter `/static/vendor/` ausgeliefert und von Bericht und statischen Seiten eingebettet (`renderer.py`). Sie ersetzen die frühere Einbindung über jsdelivr.

| Datei | Paket | Version | SHA-384 |
|---|---|---|---|
| `vis-timeline-graph2d.min.js` | vis-timeline (`standalone/umd/`) | 7.7.3 | `MLX+7yEEPNEznaIWwQi2pBMODSBP6Qo6BOVpzjFWB0UkfEzEYsvHOo7ISLfcQeiI` |
| `vis-timeline-graph2d.min.css` | vis-timeline (`styles/`) | 7.7.3 | `N/4l0y8PNlO5uZuzP9PK3bAgJQ8ZChybZFXB2mpkZRUil/bui++mvYXPzPAHpbr6` |
| `vis-network.min.js` | vis-network (`standalone/umd/`) | 9.1.9 | `yxKDWWf0wwdUj/gPeuL11czrnKFQROnLgY8ll7En9NYoXibgg3C6NK/UDHNtUgWJ` |

Lizenz beider Pakete: Apache-2.0 oder MIT, die Texte liegen daneben.

Die Hashes sind dieselben, die das Dashboard vorher als `integrity` gegen das CDN prüfte. Bei einem Update die Datei von jsdelivr oder aus dem npm-Paket holen, den Hash neu berechnen (`openssl dgst -sha384 -binary <datei> | openssl base64 -A`), Version und Hash hier eintragen und `tests/test_vendor.py` prüft die Übereinstimmung.
