# Mitgelieferte Bibliotheken

Diese Dateien sind unverändert aus den npm-Paketen übernommen und werden von der App unter `/static/vendor/` ausgeliefert und von Bericht und statischen Seiten eingebettet (`renderer.py`). Sie ersetzen die frühere Einbindung über jsdelivr.

| Datei | Paket | Version | SHA-384 |
|---|---|---|---|
| `vis-timeline-graph2d.min.js` | vis-timeline (`standalone/umd/`) | 7.7.3 | `MLX+7yEEPNEznaIWwQi2pBMODSBP6Qo6BOVpzjFWB0UkfEzEYsvHOo7ISLfcQeiI` |
| `vis-timeline-graph2d.min.css` | vis-timeline (`styles/`) | 7.7.3 | `N/4l0y8PNlO5uZuzP9PK3bAgJQ8ZChybZFXB2mpkZRUil/bui++mvYXPzPAHpbr6` |
| `vis-network.min.js` | vis-network (`standalone/umd/`) | 9.1.9 | `yxKDWWf0wwdUj/gPeuL11czrnKFQROnLgY8ll7En9NYoXibgg3C6NK/UDHNtUgWJ` |

Lizenz beider Pakete: Apache-2.0 oder MIT, die Texte liegen daneben.

## Schriften (`fonts/`)

Lateinische Teilmenge als woff2 aus den fontsource-Paketen, ersetzt Google Fonts. Lizenz: SIL Open Font License 1.1, die Texte liegen daneben. Zeichen außerhalb der Teilmenge (z. B. tschechische oder türkische Sonderzeichen) fallen auf die Systemschrift zurück.

| Datei | Paket | Version | SHA-384 |
|---|---|---|---|
| `inter-latin-wght-normal.woff2` | @fontsource-variable/inter | 5.3.0 | `l0ql7Q1zqvX5klAVRwYjcMVCkCxg5dMTdghsIlC3pYkX6wwEHerh2bP/4yAao3Im` |
| `jetbrains-mono-latin-400-normal.woff2` | @fontsource/jetbrains-mono | 5.3.0 | `/4dCc3INKaFZBsZMku6bd44i8Gr1uXW1GYvdcNjsbHXHh2nGnk9G4RAhHf8hx3GT` |
| `jetbrains-mono-latin-500-normal.woff2` | @fontsource/jetbrains-mono | 5.3.0 | `3iyOQaeJXnQxIW20oqgpGwfOVKUBAjMsLqvIIwa7iIJ+b/4hcIy0H8MDF8CKlE2b` |

Inter ist eine Variable Font (Gewicht 100 bis 900), das Dashboard nutzt 400 bis 700. Die App-Seiten außerhalb des Dashboards laden diese Schriften nicht.

Die Hashes sind dieselben, die das Dashboard vorher als `integrity` gegen das CDN prüfte. Bei einem Update die Datei von jsdelivr oder aus dem npm-Paket holen, den Hash neu berechnen (`openssl dgst -sha384 -binary <datei> | openssl base64 -A`), Version und Hash hier eintragen und `tests/test_vendor.py` prüft die Übereinstimmung.
