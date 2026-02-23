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

### 1. Descobriment de Torneigs (Manager)

```
GET /managers/{manager_id}?include=tournaments
```

**Per a què:** Descobrir tots els torneigs gestionats per la Federació Catalana de Natació (manager_id: 314965). L'endpoint retorna **tots** els torneigs (actius i finalitzats). Filtrem per `status: "in_progress"` o `status: "finished"` i els agrupem per `season_id` per gestionar múltiples temporades.

**Dades obtingudes:** Llista de torneigs amb nom, gènere, ordre, temporada i estat (`in_progress` / `finished`).

**Exemple:**
```
GET https://api.leverade.com/managers/314965?include=tournaments
```

---

### 2. Torneig (amb equips)

```
GET /tournaments/{tournament_id}?include=teams
```

**Per a què:** Per cada torneig actiu, obtenim la llista d'equips inscrits. Filtrem pels equips que pertanyen al nostre club (club_id: 4979831) per determinar en quines competicions participa el C.N. Sant Andreu.

**Dades obtingudes:** Noms, IDs i avatars dels equips, i la relació amb el club.

**Exemple:**
```
GET https://api.leverade.com/tournaments/1317476?include=teams
```

---

### 3. Torneig (amb grups)

```
GET /tournaments/{tournament_id}?include=groups
```

**Per a què:** Obtenir tots els grups (fases) d'un torneig. Cada torneig pot tenir múltiples fases (1a Fase, 2a Fase, 3a Fase, etc.) amb diversos grups a dins.

**Dades obtingudes:** ID, nom, ordre i tipus de cada grup.

**Exemple:**
```
GET https://api.leverade.com/tournaments/1317476?include=groups
```

---

### 4. Grup (amb rondes/jornades)

```
GET /groups/{group_id}?include=rounds
```

**Per a què:** Per cada grup, obtenim les seves jornades (rondes). Necessari per després demanar els partits de cada jornada.

**Dades obtingudes:** Nom, ordre, data d'inici i data de fi de cada jornada.

**Exemple:**
```
GET https://api.leverade.com/groups/3648205?include=rounds
```

---

### 5. Classificació d'un grup

```
GET /groups/{group_id}/standings
```

**Per a què:** Obtenir la taula classificatòria de cada grup amb totes les estadístiques per equip (punts, partits jugats/guanyats/empatats/perduts, gols a favor/contra).

**Dades obtingudes:** Posició, nom, ID i estadístiques de cada equip al grup.

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

**Exemple:**
```
GET https://api.leverade.com/groups/3648205/standings
```

---

### 6. Partits d'una jornada (amb resultats)

```
GET /rounds/{round_id}?include=matches.results
```

**Per a què:** Per cada jornada, obtenim tots els partits amb els seus resultats (gols per equip). Això ens dóna el calendari complet, resultats passats i partits futurs.

**Dades obtingudes:** Data, estat (acabat/cancel·lat/ajornat), equip local, equip visitant, gols de cada equip.

**Exemple:**
```
GET https://api.leverade.com/rounds/19435336?include=matches.results
```

---

### 7. Informació d'un equip

```
GET /teams/{team_id}
```

**Per a què:** Obtenir el nom d'equips que apareixen als partits però no estan a les classificacions (equips de grups que no són el nostre). S'utilitza com a fallback per resoldre noms d'equip desconeguts.

**Dades obtingudes:** Nom, avatar, club associat.

**Exemple:**
```
GET https://api.leverade.com/teams/15618241
```

---

### 8. Plantilla d'un equip (jugadors i staff)

```
GET /teams/{team_id}?include=participants.license.profile
```

**Per a què:** Obtenir la llista de jugadors i cos tècnic de cada equip. Fa una cadena d'includes: equip → participants → llicències → perfils personals.

**Dades obtingudes:**
- **Participants:** relació entre una persona i un equip dins un torneig
- **Llicències:** tipus (player/staff), número de llicència
- **Perfils:** nom, cognom, data de naixement, gènere, nacionalitat

**Exemple:**
```
GET https://api.leverade.com/teams/15618241?include=participants.license.profile
```

**Resposta (simplificada):**
```json
{
  "included": [
    {
      "type": "profile",
      "id": "2879201",
      "attributes": {
        "birthdate": "2014-03-15",
        "first_name": "MARC",
        "gender": "male",
        "last_name": "GARCIA LOPEZ",
        "nationality": "es"
      }
    },
    {
      "type": "license",
      "id": "12345678",
      "attributes": {
        "type": "player",
        "number": "1011634"
      },
      "relationships": {
        "profile": { "data": { "type": "profile", "id": "2879201" } }
      }
    },
    {
      "type": "participant",
      "id": "98765432",
      "relationships": {
        "license": { "data": { "type": "license", "id": "12345678" } },
        "participable": { "data": { "type": "tournament", "id": "1317476" } }
      }
    }
  ]
}
```

---

## Endpoints NO disponibles (requereixen autenticació)

Aquests endpoints retornen **HTTP 401** i no es poden utilitzar sense credencials:

| Endpoint | Descripció |
|---|---|
| `GET /licenses/{id}` | Detalls de llicència |
| `GET /profiles/{id}` | Perfil directe |
| `GET /matches/{id}?include=lineups` | Alineacions dels partits |
| `GET /matches/{id}?include=events` | Esdeveniments (gols, expulsions) |
| `GET /matches/{id}?include=actions` | Accions del partit |
| `GET /matches/{id}?include=scorers` | Golejadors |

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
Manager → Tournaments → Groups → Rounds → Matches → Results
                     → Teams → Participants → Licenses → Profiles
                             → Standings
```

- Un **manager** descobreix tots els **torneigs** (actius i finalitzats)
- Els torneigs s'agrupen per **temporada** (`season_id`)
- Cada **torneig** té diversos **grups** (fases) i **equips**
- Cada **grup** té diverses **rondes** (jornades) i una **classificació**
- Cada **ronda** té diversos **partits** amb **resultats**
- Cada **equip** té **participants** amb **llicències** i **perfils**

## Flux de Dades del Build (Multi-Temporada)

```
1. GET /managers/{id}?include=tournaments    → Descobrir TOTS els torneigs
   ├── Agrupar per season_id
   ├── Temporades finalitzades amb cache → Carregar de _data/seasons/{id}.json
   └── Temporades sense cache o en curs → Seguir amb passos 2-4
2. Per cada torneig (de temporades no cachejades):
   a. GET /tournaments/{id}?include=teams    → Trobar equips del nostre club
   b. GET /tournaments/{id}?include=groups   → Llistar grups/fases
3. Per cada grup:
   a. GET /groups/{id}?include=rounds        → Llistar jornades
   b. GET /groups/{id}/standings             → Classificació
   c. Per cada jornada:
      GET /rounds/{id}?include=matches.results → Partits i resultats
4. Per cada equip (de tots els grups):
   GET /teams/{id}?include=participants.license.profile → Plantilla
5. Desar cache per temporades finalitzades → _data/seasons/{id}.json
6. Generar HTML estàtic amb dades de TOTES les temporades embedded
```

## Cache de Temporades Històriques

Les temporades amb `status: "finished"` es guarden com a fitxers JSON a
`_data/seasons/{season_id}.json`. En builds posteriors, si el fitxer existeix,
**no es fan crides API** per aquella temporada (0 peticions).

Per forçar un refresh d'una temporada cachejada, esborra manualment el fitxer JSON.

## Peticions Totals per Build

- **Primera execució** (sense cache): ~450-500 peticions per temporada en curs
  + ~450-500 per cada temporada històrica nova. Total possible: ~1000+ peticions.
- **Execucions posteriors** (amb cache): ~450-500 peticions només per la temporada
  en curs. Temporades històriques: **0 peticions** (carregades de cache).

(amb 0.3s de delay entre cada petició)

## Enllaços de Referència

- Web pública: https://clupik.pro/es/tournament/1317476/summary
- Federació: https://actawp.natacio.cat/
- Equip: https://clupik.pro/es/team/15618241
