# Documentació de l'API de Leverade

## Visió General

Les dades d'aquesta web s'obtenen de l'**API pública de Leverade** (`api.leverade.com`).
[Leverade/Clupik](https://clupik.pro) és la plataforma que utilitza la
[Federació Catalana de Natació](https://actawp.natacio.cat/) per gestionar
competicions, resultats i estadístiques.

## Legalitat i Ús Responsable

- **L'API és pública**: no requereix autenticació (ni API key, ni token, ni login)
  per als endpoints que utilitzem. Qualsevol persona pot accedir-hi amb un navegador.
- **Les dades són públiques**: tota la informació (resultats, classificacions,
  calendaris) és exactament la mateixa que es mostra a les webs públiques
  [clupik.pro](https://clupik.pro) i [actawp.natacio.cat](https://actawp.natacio.cat).
- **Ús de només lectura**: no modifiquem, creem ni eliminem cap dada. Només fem
  peticions GET.
- **Ús raonable**: el build s'executa 2 cops al dia (via cron) o manualment.
  No fem polling continu ni peticions massives.
- **Sense dades privades**: no accedim a dades protegides, personals o que
  requereixin autenticació. Noms de jugadors i resultats són informació pública
  de competicions federatives.

## Base URL

```
https://api.leverade.com
```

## Endpoints Utilitzats

### 1. Torneig

```
GET /tournaments/{tournament_id}
```

Retorna informació bàsica del torneig (nom, gènere, modalitat, estat).

**Exemple:**
```
GET https://api.leverade.com/tournaments/1317476
```

**Resposta:**
```json
{
  "data": {
    "type": "tournament",
    "id": "1317476",
    "attributes": {
      "name": "LLIGA CATALANA ALEVI MIXTE",
      "gender": "mixed",
      "modality": "teams",
      "status": "in_progress"
    },
    "relationships": {
      "category": { "data": { "type": "category", "id": "3747" } },
      "discipline": { "data": { "type": "discipline", "id": "14" } },
      "manager": { "data": { "type": "manager", "id": "314965" } },
      "season": { "data": { "type": "season", "id": "8400" } }
    }
  }
}
```

---

### 2. Equip

```
GET /teams/{team_id}
```

Retorna nom, avatar i club associat.

**Exemple:**
```
GET https://api.leverade.com/teams/15618241
```

**Resposta (simplificada):**
```json
{
  "data": {
    "type": "team",
    "id": "15618241",
    "attributes": {
      "name": "C.N. SANT ANDREU B",
      "status": "confirmed"
    },
    "meta": {
      "avatar": {
        "large": "https://cdn.leverade.com/thumbnails/AaTcV9fsgKzp.500x500.jpg"
      }
    },
    "relationships": {
      "club": { "data": { "type": "club", "id": "4979831" } },
      "registrable": { "data": { "type": "tournament", "id": "1317476" } }
    }
  }
}
```

---

### 3. Grup (amb rondes)

```
GET /groups/{group_id}?include=rounds
```

Retorna la informació del grup i totes les seves jornades (rondes).

**Exemple:**
```
GET https://api.leverade.com/groups/3648205?include=rounds
```

**Resposta (simplificada):**
```json
{
  "data": {
    "type": "group",
    "id": "3648205",
    "attributes": {
      "name": "Grup Equips B 1a Fase",
      "type": "league"
    },
    "relationships": {
      "rounds": {
        "data": [
          { "type": "round", "id": "19435326" },
          { "type": "round", "id": "19435327" }
        ]
      }
    }
  },
  "included": [
    {
      "type": "round",
      "id": "19435326",
      "attributes": {
        "name": "Jornada 1",
        "order": 1,
        "start_date": "2025-10-03 22:00:00",
        "end_date": "2025-10-04 21:59:00"
      }
    }
  ]
}
```

---

### 4. Classificació d'un grup

```
GET /groups/{group_id}/standings
```

Retorna la classificació completa amb estadístiques per equip.

**Exemple:**
```
GET https://api.leverade.com/groups/3648205/standings
```

**Resposta (simplificada):**
```json
{
  "meta": {
    "standingsrows": [
      {
        "id": 15624000,
        "name": "C.N. POBLE NOU B",
        "position": 1,
        "standingsstats": [
          { "type": "score", "value": 30 },
          { "type": "played_matches", "value": 10 },
          { "type": "won_matches", "value": 10 },
          { "type": "drawn_matches", "value": 0 },
          { "type": "lost_matches", "value": 0 },
          { "type": "value", "value": 111 },
          { "type": "value_against", "value": 32 },
          { "type": "value_difference", "value": 79 }
        ]
      }
    ]
  }
}
```

**Estadístiques disponibles (`standingsstats`):**

| `type` | Significat |
|---|---|
| `score` | Punts a la classificació |
| `played_matches` | Partits jugats |
| `won_matches` | Partits guanyats |
| `drawn_matches` | Partits empatats |
| `lost_matches` | Partits perduts |
| `value` | Gols a favor |
| `value_against` | Gols en contra |
| `value_difference` | Diferència de gols |
| `penalty_shootout_periods_won` | Períodes de penalti guanyats |
| `penalty_shootout_periods_lost` | Períodes de penalti perduts |

---

### 5. Partits d'una jornada (amb resultats)

```
GET /rounds/{round_id}?include=matches.results
```

Retorna tots els partits d'una jornada amb els seus resultats (gols per equip).

**Exemple:**
```
GET https://api.leverade.com/rounds/19435336?include=matches.results
```

**Resposta (simplificada):**
```json
{
  "data": {
    "type": "round",
    "id": "19435336",
    "attributes": {
      "name": "Jornada 11",
      "order": 11,
      "start_date": "2026-02-20 23:00:00",
      "end_date": "2026-02-21 22:59:00"
    }
  },
  "included": [
    {
      "type": "result",
      "id": "190941131",
      "attributes": {
        "value": 13,
        "score": 3
      },
      "relationships": {
        "match": { "data": { "id": "143260964", "type": "match" } },
        "team": { "data": { "id": "15618241", "type": "team" } }
      }
    },
    {
      "type": "match",
      "id": "143260964",
      "attributes": {
        "date": "2026-02-21 15:45:00",
        "datetime": "2026-02-21 15:45:00",
        "display_timezone": "Europe/Madrid",
        "finished": true,
        "canceled": false,
        "postponed": false,
        "rest": false
      },
      "meta": {
        "home_team": "15618241",
        "away_team": "15621795"
      },
      "relationships": {
        "facility": { "data": { "type": "facility", "id": "74217" } },
        "round": { "data": { "type": "round", "id": "19435336" } },
        "results": {
          "data": [
            { "type": "result", "id": "190941131" },
            { "type": "result", "id": "190941138" }
          ]
        }
      }
    }
  ]
}
```

**Camp `result.attributes.value`** = gols totals de l'equip en el partit.
**Camp `result.attributes.score`** = períodes guanyats (a waterpolo aleví es juga per períodes).

---

### 6. Partits d'una jornada (amb períodes)

```
GET /rounds/{round_id}?include=matches.periods
```

Similar a l'anterior però retorna els períodes individuals de cada partit.

---

## Format de l'API

L'API segueix parcialment l'especificació [JSON:API](https://jsonapi.org/):
- `data`: recurs principal
- `included`: recursos relacionats (quan s'usa `?include=`)
- `relationships`: referències a altres recursos
- `attributes`: camps del recurs
- `meta`: metadades addicionals (ex: `home_team`, `away_team` als partits)

## Cadena de Relacions

```
Tournament → Groups → Rounds → Matches → Results
                   → Standings
```

- Un **torneig** té diversos **grups** (fases)
- Cada **grup** té diverses **rondes** (jornades)
- Cada **ronda** té diversos **partits**
- Cada **partit** té **resultats** (un per equip)
- Cada **grup** té una **classificació**

## Flux de Dades del Build

```
1. GET /tournaments/{id}           → Nom del torneig
2. GET /teams/{id}                 → Nom i avatar de l'equip
3. Per cada grup:
   a. GET /groups/{id}?include=rounds  → Llista de jornades
   b. GET /groups/{id}/standings       → Classificació
   c. Per cada jornada:
      GET /rounds/{id}?include=matches.results → Partits i resultats
4. Filtrar partits del nostre equip
5. Generar HTML estàtic
```

## Peticions Totals per Build

Per a 1 grup amb 13 jornades: **~17 peticions** (1 torneig + 1 equip + 1 grup + 1 standings + 13 jornades).

## Enllaços de Referència

- Web pública: https://clupik.pro/es/tournament/1317476/summary
- Federació: https://actawp.natacio.cat/
- Equip: https://clupik.pro/es/team/15618241
