# Water Polo Tracker – C.N. Sant Andreu

Web estàtica multi-categoria per fer seguiment de **totes** les categories de waterpolo del C.N. Sant Andreu. Extreu les dades automàticament de la [Federació Catalana de Natació](https://actawp.natacio.cat/) via l'**API pública de Leverade** (`api.leverade.com`).

🌐 **Web en viu:** [vinner21.github.io/water_follow](https://vinner21.github.io/water_follow)

> **Nota legal:** L'API de Leverade és pública (no requereix autenticació) i les dades
> que s'hi consulten (resultats, classificacions, calendaris) són informació pública
> de competicions federatives. Només fem peticions GET de lectura, 2 cops al dia.
> Vegeu [API.md](API.md) per a documentació completa de l'API.

## Funcionalitats

- **Descobriment automàtic** de totes les categories on el club participa (Aleví, Infantil, Cadet, Juvenil, Master…)
- **Navegació per pestanyes** entre categories, amb accés directe via URL hash
- **Proper Partit** amb data, hora i rival destacat
- **Resultats** amb indicadors visuals (victòria/derrota/empat) i resum d'estadístiques
- **Classificació** actualitzada de cada grup amb l'equip destacat
- **Enllaços directes** a Clupik per veure estadístiques detallades de cada equip
- **Disseny responsive** adaptat a mòbil

## Configuració

Edita `config.json` per personalitzar:

```json
{
  "club_id": "4979831",
  "club_name": "C.N. SANT ANDREU",
  "manager_id": "314965",
  "clupik_base_url": "https://clupik.pro",
  "lang": "ca"
}
```

- `club_id` – ID del club a Leverade (el script busca automàticament tots els seus equips)
- `manager_id` – ID de la federació/manager que organitza els torneigs

El script descobreix automàticament tots els torneigs actius on el club participa, sense necessitat d'indicar IDs de torneigs, equips o grups.

## Desplegament

### GitHub Pages (automàtic)

1. Fes push d'aquest repo a GitHub
2. Ves a **Settings → Pages → Source** i selecciona **GitHub Actions**
3. El workflow s'executa automàticament:
   - A cada push a `main`
   - Cada dia a les 07:00 i 20:00 UTC
   - Manualment des de **Actions → Run workflow**

### Local

```bash
pip install -r requirements.txt
python build.py
open _site/index.html
```

## Estructura

```
water/
├── config.json                 # Configuració del club
├── build.py                    # Script que genera el HTML multi-categoria
├── requirements.txt            # Dependències Python
├── API.md                      # Documentació de l'API Leverade
├── .github/workflows/build.yml # GitHub Actions (cron + deploy)
└── _site/                      # Directori generat (no commitejat)
    └── index.html
```

## Dependències

- Python 3.9+
- `requests` (única dependència)

No cal cap navegador headless ni scraping complex. Totes les dades s'obtenen via l'API pública de Leverade.

## Documentació de l'API

Vegeu **[API.md](API.md)** per a la documentació completa:
- Tots els endpoints utilitzats amb exemples de request/response
- Explicació del format JSON:API
- Cadena de relacions entre entitats
- Flux de dades del build
- Secció de legalitat i ús responsable
