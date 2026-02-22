#!/usr/bin/env python3
"""
Build script for Water Polo Tracker - Multi-Category.

Fetches data from the Leverade API (used by clupik.pro / Federacio Catalana
de Natacio) and generates a static HTML site with all water-polo categories
where the configured club has teams.
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from html import escape

import requests

API_BASE = "https://api.leverade.com"
CLUPIK_BASE = "https://clupik.pro"
REQUEST_DELAY = 0.3


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def api_get(endpoint, params=None):
    url = f"{API_BASE}/{endpoint}"
    time.sleep(REQUEST_DELAY)
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_tournaments(manager_id, club_id):
    print("Fetching manager tournaments ...")
    data = api_get(f"managers/{manager_id}", params={"include": "tournaments"})
    in_progress = []
    for inc in data.get("included", []):
        if inc["type"] != "tournament":
            continue
        attrs = inc["attributes"]
        if attrs["status"] != "in_progress":
            continue
        season_data = inc["relationships"].get("season", {}).get("data")
        in_progress.append({
            "id": inc["id"], "name": attrs["name"],
            "gender": attrs.get("gender"), "order": attrs.get("order"),
            "season_id": season_data["id"] if season_data else None,
        })
    print(f"  Found {len(in_progress)} in-progress tournaments")

    tournaments_with_us = []
    for t in in_progress:
        print(f"  Checking {t['name']} ...", end=" ")
        try:
            tdata = api_get(f"tournaments/{t['id']}", params={"include": "teams"})
        except Exception as e:
            print(f"SKIP ({e})")
            continue
        our_teams = []
        for inc in tdata.get("included", []):
            if inc["type"] != "team":
                continue
            club_data = inc.get("relationships", {}).get("club", {}).get("data", {})
            if club_data and club_data.get("id") == club_id:
                avatar = inc.get("meta", {}).get("avatar", {}).get("large", "")
                our_teams.append({"id": inc["id"], "name": inc["attributes"]["name"], "avatar": avatar})
        if our_teams:
            t["our_teams"] = our_teams
            tournaments_with_us.append(t)
            print(f"OK ({', '.join(tm['name'] for tm in our_teams)})")
        else:
            print("-")

    tournaments_with_us.sort(key=lambda t: t.get("order") or 999)
    print(f"\n-> Club participates in {len(tournaments_with_us)} tournaments\n")
    return tournaments_with_us


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def get_tournament_groups(tournament_id):
    data = api_get(f"tournaments/{tournament_id}", params={"include": "groups"})
    groups = []
    for inc in data.get("included", []):
        if inc["type"] == "group":
            groups.append({
                "id": inc["id"], "name": inc["attributes"]["name"],
                "order": inc["attributes"]["order"], "type": inc["attributes"]["type"],
            })
    groups.sort(key=lambda g: g["order"] or 0)
    return groups


def get_group_with_rounds(gid):
    data = api_get(f"groups/{gid}", params={"include": "rounds"})
    group = {"id": gid, "name": data["data"]["attributes"]["name"], "rounds": []}
    for inc in data.get("included", []):
        if inc["type"] == "round":
            group["rounds"].append({
                "id": inc["id"], "name": inc["attributes"]["name"],
                "order": inc["attributes"]["order"],
                "start_date": inc["attributes"]["start_date"],
                "end_date": inc["attributes"]["end_date"],
            })
    group["rounds"].sort(key=lambda r: r["order"])
    return group


def get_round_matches(rid):
    data = api_get(f"rounds/{rid}", params={"include": "matches.results,matches.facility"})
    results_map = {}
    facilities_map = {}
    matches = []
    for inc in data.get("included", []):
        if inc["type"] == "result":
            results_map[inc["id"]] = {
                "value": inc["attributes"]["value"],
                "score": inc["attributes"]["score"],
                "team_id": inc["relationships"]["team"]["data"]["id"],
                "match_id": inc["relationships"]["match"]["data"]["id"],
            }
        elif inc["type"] == "facility":
            facilities_map[inc["id"]] = inc["attributes"].get("name", "")
        elif inc["type"] == "match":
            meta = inc.get("meta", {})
            fac_ref = inc.get("relationships", {}).get("facility", {}).get("data")
            match = {
                "id": inc["id"], "date": inc["attributes"]["date"],
                "finished": inc["attributes"]["finished"],
                "canceled": inc["attributes"]["canceled"],
                "postponed": inc["attributes"]["postponed"],
                "rest": inc["attributes"].get("rest", False),
                "home_team": meta.get("home_team"),
                "away_team": meta.get("away_team"),
                "facility_id": fac_ref["id"] if fac_ref else None,
                "results": [],
            }
            for res_ref in inc.get("relationships", {}).get("results", {}).get("data", []):
                r = results_map.get(res_ref["id"])
                if r:
                    match["results"].append(r)
            matches.append(match)
    # Resolve facility names
    for m in matches:
        fid = m.pop("facility_id", None)
        m["venue"] = facilities_map.get(fid, "") if fid else ""
    return matches


def get_standings(gid):
    data = api_get(f"groups/{gid}/standings")
    standings = []
    for row in data.get("meta", {}).get("standingsrows", []):
        stats = {s["type"]: s["value"] for s in row.get("standingsstats", [])}
        standings.append({
            "id": row["id"], "name": row["name"], "position": row["position"],
            "points": stats.get("score", 0),
            "played": stats.get("played_matches", 0),
            "won": stats.get("won_matches", 0),
            "drawn": stats.get("drawn_matches", 0),
            "lost": stats.get("lost_matches", 0),
            "goals_for": stats.get("value", 0),
            "goals_against": stats.get("value_against", 0),
            "goal_diff": stats.get("value_difference", 0),
        })
    standings.sort(key=lambda s: s["position"])
    return standings


def get_team_roster(team_id):
    """Fetch player/staff roster for a team via participants.license.profile."""
    data = api_get(f"teams/{team_id}", params={"include": "participants.license.profile"})
    included = data.get("included", [])
    profiles = {i["id"]: i["attributes"] for i in included if i["type"] == "profile"}
    licenses = {i["id"]: i for i in included if i["type"] == "license"}
    participants = [i for i in included if i["type"] == "participant"]
    roster = []
    for p in participants:
        lic_ref = p.get("relationships", {}).get("license", {}).get("data")
        if not lic_ref:
            continue
        lic = licenses.get(lic_ref["id"], {})
        lic_type = lic.get("attributes", {}).get("type", "unknown")
        profile_ref = lic.get("relationships", {}).get("profile", {}).get("data")
        profile = profiles.get(profile_ref["id"], {}) if profile_ref else {}
        if not profile.get("first_name"):
            continue
        roster.append({
            "first_name": profile.get("first_name", ""),
            "last_name": profile.get("last_name", ""),
            "birthdate": profile.get("birthdate"),
            "role": lic_type,
        })
    # Sort: players first (sorted by last_name), then staff
    roster.sort(key=lambda r: (0 if r["role"] == "player" else 1, r["last_name"], r["first_name"]))
    return roster


def collect_tournament_data(tournament, club_id):
    tid = tournament["id"]
    our_team_ids = {t["id"] for t in tournament["our_teams"]}
    print(f"  Fetching groups for {tournament['name']} ...")
    groups = get_tournament_groups(tid)
    print(f"    {len(groups)} groups found")

    collected_groups = []
    all_matches = []
    team_names = {}

    for g in groups:
        gid = g["id"]
        print(f"    Fetching group {g['name']} ...", end=" ")
        standings = get_standings(gid)
        standing_team_ids = set()
        for row in standings:
            team_names[str(row["id"])] = row["name"]
            standing_team_ids.add(str(row["id"]))
        our_in_group = our_team_ids & standing_team_ids

        group_detail = get_group_with_rounds(gid)
        group_matches = []
        for rnd in group_detail["rounds"]:
            matches = get_round_matches(rnd["id"])
            for m in matches:
                m["round_name"] = rnd["name"]
                m["round_order"] = rnd["order"]
                m["group_id"] = gid
                m["group_name"] = g["name"]
            group_matches.extend(matches)

        collected_groups.append({
            "id": gid, "name": g["name"],
            "standings": standings, "our_team_ids": our_in_group,
        })
        all_matches.extend(group_matches)
        print(f"{len(group_matches)} matches" + (f" (our team)" if our_in_group else ""))

    missing_ids = set()
    for m in all_matches:
        if m["home_team"] and m["home_team"] not in team_names:
            missing_ids.add(m["home_team"])
        if m["away_team"] and m["away_team"] not in team_names:
            missing_ids.add(m["away_team"])
    for mid in missing_ids:
        try:
            tdata = api_get(f"teams/{mid}")
            team_names[mid] = tdata["data"]["attributes"]["name"]
        except Exception:
            team_names[mid] = f"Equip {mid}"
    for t in tournament["our_teams"]:
        team_names[t["id"]] = t["name"]

    all_matches.sort(key=lambda m: m["date"] or "9999")

    # Fetch rosters for ALL teams across all groups
    all_team_ids_in_groups = set()
    for g in collected_groups:
        for row in g["standings"]:
            all_team_ids_in_groups.add(str(row["id"]))
    rosters = {}
    print(f"    Fetching rosters for {len(all_team_ids_in_groups)} teams ...")
    for t_id in sorted(all_team_ids_in_groups):
        try:
            rosters[t_id] = get_team_roster(t_id)
        except Exception as e:
            print(f"      Warning: could not fetch roster for {t_id}: {e}")
            rosters[t_id] = []
    print(f"    Rosters: {sum(len(r) for r in rosters.values())} total participants")

    return {
        "tournament_id": tid, "tournament_name": tournament["name"],
        "our_teams": tournament["our_teams"], "our_team_ids": our_team_ids,
        "groups": collected_groups, "matches": all_matches, "team_names": team_names,
        "rosters": rosters,
    }


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def format_date(date_str):
    if not date_str:
        return "Per determinar"
    dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    days_ca = ["Dl", "Dt", "Dc", "Dj", "Dv", "Ds", "Dg"]
    return f"{days_ca[dt.weekday()]} {dt.day:02d}/{dt.month:02d}/{dt.year} {dt.hour:02d}:{dt.minute:02d}"


def format_date_short(date_str):
    if not date_str:
        return "TBD"
    dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    return f"{dt.day:02d}/{dt.month:02d} {dt.hour:02d}:{dt.minute:02d}"


def match_score(match):
    home_score = away_score = None
    for r in match["results"]:
        if r["team_id"] == match["home_team"]:
            home_score = r["value"]
        elif r["team_id"] == match["away_team"]:
            away_score = r["value"]
    return home_score, away_score


def match_result_class(match, our_team_ids):
    if not match["finished"]:
        return "upcoming"
    hs, aws = match_score(match)
    if hs is None or aws is None:
        return "unknown"
    is_home = match["home_team"] in our_team_ids
    ours = hs if is_home else aws
    theirs = aws if is_home else hs
    if ours > theirs:
        return "win"
    elif ours < theirs:
        return "loss"
    return "draw"


def short_category(name):
    name = name.replace("LLIGA CATALANA ", "").replace("COMPETICIO CATALANA ", "").replace("COMPETICIÓ CATALANA ", "")
    # Order matters: longer patterns first to avoid partial replacements
    for old, new in [("MASCULINA DE PROMOCIO", "Promo Masc."), ("MASCULINA DE PROMOCIÓ", "Promo Masc."),
                     ("MASCULINA", "Masc."), ("MASCULI", "Masc."), ("MASCULÍ", "Masc."),
                     ("FEMENINA", "Fem."), ("FEMENI", "Fem."), ("FEMENÍ", "Fem."),
                     ("MIXTE", "Mixt"), ("MIXTA", "Mixt"), ("BENJAMINA", "Benjamí"),
                     ("MASTER", "Màster")]:
        name = name.replace(old, new)
    return name.strip()


# Age categories for Catalan water polo – season 2025-2026
# Source: FCN Normativa 01 – Disposicions Generals (edats de competició)
# Order: lower = younger. Used for sorting and age labels on cards.
CATEGORY_AGE = {
    "BENJAMI":  (1, "9-10 anys (2016-17)"),
    "ALEVI":    (2, "11-12 anys (2014-15)"),
    "INFANTIL": (3, "13-14 anys (2012-13)"),
    "CADET":    (4, "15-16 anys (2010-11)"),
    "JUVENIL":  (5, "17-18 anys (2008-09)"),
    "ABSOLUTA": (6, "+18 anys"),
    "MASTER":   (7, "+30 anys"),
}

def category_age_info(tournament_name):
    """Return (sort_order, age_label) for a tournament name."""
    upper = tournament_name.upper()
    for key, (order, label) in CATEGORY_AGE.items():
        if key in upper:
            return order, label
    return 99, ""


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

CSS = """
:root {
  --blue: #0077b6; --blue-dark: #023e8a; --blue-light: #90e0ef;
  --blue-pale: #caf0f8; --green: #2d6a4f; --red: #9d0208;
  --orange: #e09f3e; --bg: #f0f2f5; --card: #fff;
  --text: #212529; --text-muted: #6c757d; --radius: 10px;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);line-height:1.5}
header{background:linear-gradient(135deg,var(--blue-dark),var(--blue));color:#fff;padding:1.2rem 1rem .8rem;text-align:center}
.header-inner{display:flex;align-items:center;justify-content:center;gap:.75rem}
.club-logo{width:52px;height:52px;border-radius:50%;border:2px solid rgba(255,255,255,.6);flex-shrink:0}
header h1{font-size:1.3rem;font-weight:700}.subtitle{font-size:.8rem;opacity:.8}
main{max-width:780px;margin:0 auto;padding:.75rem}

/* Selection screen */
#selection-screen{display:block}
#detail-screen{display:none}
#team-screen{display:none}
.sel-title{text-align:center;font-size:1rem;color:var(--blue-dark);margin:.8rem 0 .2rem;font-weight:600}
.sel-subtitle{text-align:center;font-size:.82rem;color:var(--text-muted);margin-bottom:.6rem}
.cat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:.6rem;padding:0 .2rem}
.cat-card{background:var(--card);border-radius:var(--radius);padding:.8rem;cursor:pointer;border:2px solid transparent;transition:border-color .2s,box-shadow .2s,transform .15s;position:relative;overflow:hidden}
.cat-card:hover{border-color:var(--blue-light);box-shadow:0 4px 12px rgba(0,119,182,.15);transform:translateY(-2px)}
.cat-card-name{font-size:.85rem;font-weight:700;color:var(--blue-dark);margin-bottom:.15rem}
.cat-card-age{font-size:.7rem;color:var(--text-muted);margin-bottom:.25rem;font-style:italic}
.cat-card-teams{font-size:.75rem;color:var(--text-muted);margin-bottom:.35rem}
.cat-card-record{display:inline-flex;gap:.3rem;font-size:.7rem}
.cat-card-record span{padding:.1rem .35rem;border-radius:3px;font-weight:600}
.cat-card-record .w{background:#d4edda;color:var(--green)}.cat-card-record .d{background:#fff3cd;color:#856404}
.cat-card-record .l{background:#f8d7da;color:var(--red)}.cat-card-record .gf{background:var(--blue-pale);color:var(--blue-dark)}
.cat-card-next{font-size:.72rem;color:var(--text-muted);margin-top:.3rem;border-top:1px solid #eee;padding-top:.3rem}
.cat-card-next strong{color:var(--blue)}
.cat-card-arrow{position:absolute;right:.6rem;top:50%;transform:translateY(-50%);font-size:1.2rem;color:var(--blue-light);opacity:.5}

/* Detail screen */
.back-bar{background:var(--card);border-bottom:1px solid #e0e0e0;padding:.5rem .8rem;display:flex;align-items:center;gap:.5rem}
.btn-back{background:none;border:1px solid var(--blue);color:var(--blue);padding:.3rem .7rem;border-radius:6px;font-size:.8rem;cursor:pointer;display:flex;align-items:center;gap:.3rem;transition:background .2s,color .2s}
.btn-back:hover{background:var(--blue);color:#fff}
.back-label{font-size:.82rem;color:var(--text-muted)}

.category-header{background:var(--card);border-radius:var(--radius);padding:1rem;margin-bottom:.6rem;text-align:center}
.category-header h2{font-size:1rem;color:var(--blue-dark);margin-bottom:.2rem}
.team-selector-wrap{margin:.4rem 0;display:flex;align-items:center;justify-content:center;gap:.4rem;flex-wrap:wrap}
.team-selector-label{font-size:.75rem;color:var(--text-muted)}
.team-selector{font-size:.8rem;padding:.3rem .5rem;border:1px solid var(--blue-light);border-radius:6px;color:var(--blue-dark);background:#fff;cursor:pointer;max-width:260px}
.team-selector:focus{outline:none;border-color:var(--blue);box-shadow:0 0 0 2px rgba(0,119,182,.2)}
.teams-label{font-size:.8rem;color:var(--text-muted);margin-bottom:.4rem}
.record-bar{display:inline-flex;gap:.4rem;font-size:.78rem}
.record-bar span{padding:.12rem .45rem;border-radius:4px;font-weight:600}
.record-bar .w{background:#d4edda;color:var(--green)}.record-bar .d{background:#fff3cd;color:#856404}
.record-bar .l{background:#f8d7da;color:var(--red)}.record-bar .gf{background:var(--blue-pale);color:var(--blue-dark)}
.record-bar .ga{background:#e9ecef;color:var(--text-muted)}
.section-block{background:var(--card);border-radius:var(--radius);padding:.8rem;margin-bottom:.6rem}
.section-block h3{font-size:.9rem;color:var(--blue-dark);border-bottom:2px solid var(--blue-pale);padding-bottom:.25rem;margin-bottom:.5rem;cursor:pointer;user-select:none;display:flex;align-items:center;justify-content:space-between}
.section-block h3 .toggle-arrow{font-size:.55rem;color:var(--blue-light);transition:transform .2s;display:inline-block}
.section-block.collapsed h3 .toggle-arrow{transform:rotate(180deg)}
.section-block.collapsed .section-content{display:none}
.empty{color:var(--text-muted);font-size:.85rem}
.next-match-card{background:linear-gradient(135deg,var(--blue),var(--blue-dark));color:#fff;border-radius:8px;padding:1rem;text-align:center}
.next-date{font-size:.85rem;opacity:.85;margin-bottom:.35rem}
.next-teams{font-size:1.2rem;font-weight:700}
.next-teams .vs{margin:0 .4rem;opacity:.6;font-weight:400;font-size:.9rem}
.next-round{font-size:.75rem;opacity:.65;margin-top:.2rem}
.our-team{color:var(--blue)}.next-match-card .our-team{color:#ffd166}
.match-row{padding:.5rem .6rem;border-radius:6px;margin-bottom:.3rem;border-left:4px solid transparent;background:var(--bg);transition:box-shadow .15s}
.match-row:hover{box-shadow:0 1px 6px rgba(0,0,0,.06)}
.match-row.win{border-left-color:var(--green)}.match-row.loss{border-left-color:var(--red)}
.match-row.draw{border-left-color:var(--orange)}.match-row.upcoming{border-left-color:var(--blue-light);background:var(--blue-pale)}
.match-meta{display:flex;gap:.5rem;font-size:.7rem;color:var(--text-muted);margin-bottom:.2rem;flex-wrap:wrap}
.match-venue{font-size:.68rem;color:var(--text-muted);font-style:italic;margin-top:.1rem}
.match-teams{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:.3rem;font-size:.82rem}
.team-home{text-align:right;font-weight:500}.team-away{text-align:left;font-weight:500}
.match-score{display:flex;align-items:center;gap:.15rem;font-weight:700;font-size:.9rem;justify-content:center}
.score-sep{color:var(--text-muted);font-size:.8rem}
.vs-small{color:var(--text-muted);font-size:.78rem}
.standings-block{margin-bottom:.6rem}
.standings-block h3{font-size:.82rem;color:var(--blue);margin-bottom:.3rem;border:none;padding:0}
.phase-header{font-size:.82rem;color:var(--blue);font-weight:600;margin:.7rem 0 .3rem;padding-bottom:.2rem;border-bottom:1px solid var(--blue)}
.phase-header:first-child{margin-top:0}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:.75rem}
th,td{padding:.35rem .3rem;text-align:center}
th{background:var(--blue-dark);color:#fff;font-weight:600;font-size:.7rem;position:sticky;top:0}
td{border-bottom:1px solid #e9ecef}
.team-name-cell{text-align:left!important;white-space:nowrap}
.pos{font-weight:700;color:var(--blue)}.pts{font-weight:700;color:var(--blue-dark)}
tr.highlight{background:var(--blue-pale)}tr.highlight td{font-weight:600}
.links-block{display:flex;flex-wrap:wrap;gap:.5rem;justify-content:center}
.btn-link{padding:.35rem .7rem;background:var(--blue);color:#fff;text-decoration:none;border-radius:6px;font-size:.78rem}
.btn-link:hover{background:var(--blue-dark)}
.roster-table{width:100%;border-collapse:collapse;font-size:.78rem}
.roster-table th{background:var(--blue-dark);color:#fff;font-weight:600;font-size:.72rem;padding:.35rem .4rem;text-align:left}
.roster-table td{padding:.3rem .4rem;border-bottom:1px solid #e9ecef}
.roster-name{font-weight:500}
.roster-role{color:var(--text-muted);font-size:.72rem;font-style:italic}
.roster-staff-title{font-size:.8rem;color:var(--blue);font-weight:600;margin:.6rem 0 .3rem;padding-top:.4rem;border-top:1px solid #e9ecef}
footer{text-align:center;padding:1.2rem 1rem;font-size:.72rem;color:var(--text-muted)}
footer a{color:var(--blue)}
.search-wrap{margin:0 auto .8rem;max-width:500px;position:relative}
.search-input{width:100%;padding:.5rem .8rem .5rem 2rem;border:1px solid var(--blue-light);border-radius:8px;font-size:.85rem;background:var(--card);color:var(--text);outline:none;box-sizing:border-box}
.search-input:focus{border-color:var(--blue);box-shadow:0 0 0 2px rgba(0,119,182,.15)}
.search-icon{position:absolute;left:.6rem;top:50%;transform:translateY(-50%);font-size:.85rem;color:var(--text-muted);pointer-events:none}
.search-clear{position:absolute;right:.5rem;top:50%;transform:translateY(-50%);background:none;border:none;font-size:1rem;color:var(--text-muted);cursor:pointer;display:none;padding:0 .2rem}
.search-results{background:var(--card);border-radius:var(--radius);margin-top:.4rem;max-height:70vh;overflow-y:auto}
.search-result-item{padding:.6rem .8rem;border-bottom:1px solid #e9ecef;cursor:default}
.search-result-item:last-child{border-bottom:none}
.search-result-name{font-weight:600;font-size:.85rem;color:var(--blue-dark)}
.search-result-role{font-size:.72rem;color:var(--text-muted);font-style:italic;margin-left:.3rem}
.search-result-teams{margin-top:.2rem}
.search-result-tag{display:inline-block;font-size:.7rem;background:var(--blue-pale);color:var(--blue-dark);padding:.1rem .4rem;border-radius:4px;margin:.1rem .2rem .1rem 0;cursor:pointer}
.search-result-tag:hover{background:var(--blue);color:#fff}
.search-result-by{font-size:.72rem;color:var(--text-muted);margin-left:.3rem}
.search-empty{padding:.8rem;text-align:center;color:var(--text-muted);font-size:.82rem}
@media(max-width:480px){.cat-grid{grid-template-columns:1fr}.match-row{grid-template-columns:52px 56px 1fr;padding:.4rem}.match-teams{font-size:.74rem}header h1{font-size:1.1rem}}
"""

JS = """
/* --- Helpers --- */
function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML;}
function titleCase(s){return s.split(' ').map(function(w){return w.charAt(0).toUpperCase()+w.slice(1).toLowerCase();}).join(' ');}
function toggleSection(h3){h3.parentElement.classList.toggle('collapsed');}

/* --- Player Search --- */
var _searchIdx=null;
function buildSearchIndex(){
  if(_searchIdx)return _searchIdx;
  /* Build team→tournament map from WP */
  var teamTournMap={};
  Object.keys(window.WP).forEach(function(eid){
    var d=window.WP[eid];
    Object.keys(d.teams).forEach(function(tid){
      if(!teamTournMap[tid])teamTournMap[tid]=[];
      teamTournMap[tid].push({eid:eid,tname:d.tname,label:d.label||d.tname,teamName:d.teams[tid]});
    });
  });
  /* Build person index from ROST */
  var persons={};/* key: fn|ln|bd */
  var rost=window.ROST||{};
  Object.keys(rost).forEach(function(tid){
    rost[tid].forEach(function(p){
      var k=p.fn+'|'+p.ln+'|'+(p.bd||'');
      if(!persons[k])persons[k]={fn:p.fn,ln:p.ln,bd:p.bd,ro:p.ro,teams:[]};
      /* merge role (player > staff) */
      if(p.ro==='player')persons[k].ro='player';
      var tours=teamTournMap[tid]||[];
      tours.forEach(function(t){
        var already=persons[k].teams.some(function(x){return x.eid===t.eid&&x.teamName===t.teamName;});
        if(!already)persons[k].teams.push({eid:t.eid,tname:t.tname,label:t.label,teamName:t.teamName});
      });
    });
  });
  _searchIdx=Object.values(persons);
  /* Pre-compute search text */
  _searchIdx.forEach(function(p){
    p._s=(p.fn+' '+p.ln).toLowerCase();
  });
  return _searchIdx;
}
function doSearch(q){
  var res=document.getElementById('search-results');
  var clear=document.getElementById('search-clear');
  if(!q||q.length<2){res.innerHTML='';res.style.display='none';clear.style.display='none';return;}
  clear.style.display='block';
  var idx=buildSearchIndex();
  var ql=q.toLowerCase().trim();
  var words=ql.split(/\s+/);
  var hits=idx.filter(function(p){
    return words.every(function(w){return p._s.indexOf(w)>=0;});
  });
  if(hits.length===0){res.innerHTML='<div class="search-empty">Cap resultat per \"'+esc(q)+'\"</div>';res.style.display='block';return;}
  if(hits.length>50)hits=hits.slice(0,50);
  var html='';
  hits.forEach(function(p){
    var name=esc(titleCase(p.fn)+' '+titleCase(p.ln));
    var role=p.ro==='player'?'Jugador':'Staff';
    var by=p.bd?p.bd.substring(0,4):'';
    var byH=by?' <span class="search-result-by">('+by+')</span>':'';
    var tags='';var seenT={};
    p.teams.forEach(function(t){
      var lbl=t.label||t.tname;
      if(seenT[lbl])return;seenT[lbl]=true;
      tags+='<span class="search-result-tag" onclick="clearSearch();showDetail(\\''+t.eid+'\\')" title="'+esc(t.teamName)+'">'+esc(lbl)+'</span>';
    });
    html+='<div class="search-result-item"><div><span class="search-result-name">'+name+'</span>'+byH+'<span class="search-result-role">'+role+'</span></div><div class="search-result-teams">'+tags+'</div></div>';
  });
  if(hits.length>=50)html+='<div class="search-empty">Mostrant 50 de mes resultats...</div>';
  res.innerHTML=html;res.style.display='block';
}
function clearSearch(){
  var inp=document.getElementById('search-input');
  inp.value='';doSearch('');
}
function fmtShort(ds){
  if(!ds)return'TBD';
  var p=ds.split(/[- :]/);return p[2]+'/'+p[1]+' '+p[3]+':'+p[4];
}
function fmtLong(ds){
  if(!ds)return'Per determinar';
  var dt=new Date(ds.replace(' ','T'));
  var days=['Dg','Dl','Dt','Dc','Dj','Dv','Ds'];
  var d=('0'+dt.getDate()).slice(-2),mo=('0'+(dt.getMonth()+1)).slice(-2);
  var h=('0'+dt.getHours()).slice(-2),mi=('0'+dt.getMinutes()).slice(-2);
  return days[dt.getDay()]+' '+d+'/'+mo+'/'+dt.getFullYear()+' '+h+':'+mi;
}

/* --- Navigation --- */
function showScreen(name){
  ['selection-screen','team-screen','detail-screen'].forEach(function(s){
    document.getElementById(s).style.display=s===name?'block':'none';
  });
  window.scrollTo(0,0);
}
function showCategories(){showScreen('selection-screen');history.replaceState(null,'','#');}
function showTeams(catId){
  showScreen('team-screen');
  document.querySelectorAll('.team-panel').forEach(function(p){p.style.display='none';});
  var el=document.getElementById('teams-'+catId);
  if(el)el.style.display='block';
  history.replaceState(null,'','#cat-'+catId);
}
function showDetail(id){
  showScreen('detail-screen');
  document.querySelectorAll('.detail-category').forEach(function(c){c.style.display='none';});
  var el=document.getElementById(id);
  if(el){
    el.style.display='block';
    var catId=el.dataset.catId,numTeams=parseInt(el.dataset.numTeams)||1;
    var catLabel=el.dataset.catLabel||'';
    var btn=document.getElementById('detail-back-btn');
    var lbl=document.getElementById('detail-back-label');
    if(numTeams>1){btn.onclick=function(){showTeams(catId);};lbl.textContent=catLabel;}
    else{btn.onclick=function(){showCategories();};lbl.textContent='Totes les categories';}
    /* Render default team */
    var data=window.WP[id];
    if(data){
      var sel=el.querySelector('.team-selector');
      if(sel)sel.value=data.dt;
      renderForTeam(id,data.dt);
    }
  }
  history.replaceState(null,'','#'+id);
}
function showDetailOrTeams(catId,teamCount){
  if(teamCount===1){
    var panel=document.getElementById('teams-'+catId);
    if(panel){var b=panel.querySelector('[data-detail]');if(b)showDetail(b.dataset.detail);}
  } else {showTeams(catId);}
}

/* --- Dynamic Renderer --- */
function renderForTeam(entryId,teamId){
  var data=window.WP[entryId];
  if(!data)return;
  var tids=new Set([teamId]);
  var teamName=data.teams[teamId]||'Equip';
  var clupik=window.CLUPIK||'https://clupik.pro';

  /* Filter and sort matches */
  var teamMatches=data.matches.filter(function(m){return tids.has(m.h)||tids.has(m.a);});
  var past=teamMatches.filter(function(m){return m.f;}).sort(function(a,b){return(b.d||'').localeCompare(a.d||'');});
  var future=teamMatches.filter(function(m){return !m.f&&m.d;}).sort(function(a,b){return(a.d||'').localeCompare(b.d||'');});

  /* Stats */
  var w=0,dr=0,lo=0,gf=0,gc=0;
  past.forEach(function(m){
    var isH=tids.has(m.h),os=isH?m.hs:m.as,ts=isH?m.as:m.hs;
    if(os!=null&&ts!=null){gf+=os;gc+=ts;if(os>ts)w++;else if(os<ts)lo++;else dr++;}
  });

  /* Record bar */
  document.getElementById('record-'+entryId).innerHTML=
    '<span class="w">'+w+'V</span><span class="d">'+dr+'E</span>'+
    '<span class="l">'+lo+'D</span><span class="gf">'+gf+'GF</span>'+
    '<span class="ga">'+gc+'GC</span>';

  /* Next match */
  var nextH='';
  if(future.length>0){
    var nm=future[0],hN=esc(data.teams[nm.h]||'?'),aN=esc(data.teams[nm.a]||'Descansa');
    var isH=tids.has(nm.h);
    var venueNext=nm.v?'<div class="next-round" style="font-style:italic">'+esc(nm.v)+'</div>':'';
    nextH='<div class="section-block collapsed"><h3 onclick="toggleSection(this)">Proper Partit<span class="toggle-arrow">\u25B2</span></h3>'+
      '<div class="section-content"><div class="next-match-card">'+
      '<div class="next-date">'+fmtLong(nm.d)+'</div>'+
      '<div class="next-teams">'+
      '<span class="'+(isH?'our-team':'')+'">'+ hN+'</span>'+
      '<span class="vs">vs</span>'+
      '<span class="'+(!isH?'our-team':'')+'">'+ aN+'</span>'+
      '</div><div class="next-round">'+esc(nm.rn)+'</div>'+venueNext+'</div></div></div>';
  }
  document.getElementById('next-'+entryId).innerHTML=nextH;

  /* Standings – only show groups where selected team appears */
  var stH='';
  data.groups.forEach(function(g){
    var inGroup=g.s.some(function(s){return s.id===teamId;});
    if(!inGroup)return;
    var rows='';
    g.s.forEach(function(s){
      var hl=s.id===teamId?' class="highlight"':'';
      rows+='<tr'+hl+'><td class="pos">'+s.pos+'</td><td class="team-name-cell">'+esc(s.n)+'</td>'+
        '<td>'+s.pj+'</td><td>'+s.pg+'</td><td>'+s.pe+'</td><td>'+s.pp+'</td>'+
        '<td>'+s.gf+'</td><td>'+s.gc+'</td><td>'+(s.dg>=0?'+':'')+s.dg+'</td>'+
        '<td class="pts">'+s.pts+'</td></tr>';
    });
    stH+='<div class="standings-block"><h3>'+esc(g.n)+'</h3>'+
      '<div class="table-wrap"><table><thead><tr>'+
      '<th>#</th><th>Equip</th><th>PJ</th><th>PG</th><th>PE</th>'+
      '<th>PP</th><th>GF</th><th>GC</th><th>DG</th><th>Pts</th>'+
      '</tr></thead><tbody>'+rows+'</tbody></table></div></div>';
  });
  document.getElementById('standings-'+entryId).innerHTML=stH||'<p class="empty">Classificacio no disponible.</p>';

  /* Results – grouped by phase/group */
  var rH='';
  if(past.length===0){rH='<p class="empty">Encara no hi ha resultats.</p>';}
  else{
    var phaseOrder=[];var phaseMap={};
    past.forEach(function(m){
      var ph=m.gn||'Resultats';
      if(!phaseMap[ph]){phaseMap[ph]=[];phaseOrder.push(ph);}
      phaseMap[ph].push(m);
    });
    var multiPhase=phaseOrder.length>1;
    phaseOrder.forEach(function(ph){
      if(multiPhase)rH+='<div class="phase-header">'+esc(ph)+'</div>';
      phaseMap[ph].forEach(function(m){
        var isH=tids.has(m.h),os=isH?m.hs:m.as,ts=isH?m.as:m.hs;
        var cls='';if(os!=null&&ts!=null){cls=os>ts?'win':os<ts?'loss':'draw';}
        var hN=esc(data.teams[m.h]||'?'),aN=esc(data.teams[m.a]||'Descansa');
        var venueR=m.v?'<div class="match-venue">'+esc(m.v)+'</div>':'';
        rH+='<div class="match-row '+cls+'">'+
          '<div class="match-meta"><span>'+fmtShort(m.d)+'</span><span>'+esc(m.rn)+'</span></div>'+
          '<div class="match-teams">'+
          '<span class="team-home'+(isH?' our-team':'')+'">'+ hN+'</span>'+
          '<span class="match-score"><span>'+(m.hs!=null?m.hs:'-')+'</span>'+
          '<span class="score-sep">-</span>'+
          '<span>'+(m.as!=null?m.as:'-')+'</span></span>'+
          '<span class="team-away'+(!isH?' our-team':'')+'">'+ aN+'</span>'+
          '</div>'+venueR+'</div>';
      });
    });
  }
  document.getElementById('results-'+entryId).innerHTML=rH;

  /* Upcoming */
  var uH='';
  var uList=future;
  if(uList.length>0){
    var items='';
    uList.forEach(function(m){
      var isH=tids.has(m.h);
      var hN=esc(data.teams[m.h]||'?'),aN=esc(data.teams[m.a]||'Descansa');
      var venueU=m.v?'<div class="match-venue">'+esc(m.v)+'</div>':'';
      items+='<div class="match-row upcoming">'+
        '<div class="match-meta"><span>'+fmtShort(m.d)+'</span><span>'+esc(m.rn)+'</span></div>'+
        '<div class="match-teams">'+
        '<span class="team-home'+(isH?' our-team':'')+'">'+hN+'</span>'+
        '<span class="vs-small">vs</span>'+
        '<span class="team-away'+(!isH?' our-team':'')+'">'+aN+'</span>'+
        '</div>'+venueU+'</div>';
    });
    uH='<div class="section-block collapsed"><h3 onclick="toggleSection(this)">Propers Partits<span class="toggle-arrow">\u25B2</span></h3>'+
      '<div class="section-content">'+items+'</div></div>';
  }
  document.getElementById('upcoming-'+entryId).innerHTML=uH;

  /* Roster */
  var rosH='';
  var roster=window.ROST&&window.ROST[teamId];
  if(roster&&roster.length>0){
    /* Deduplicate by fn+ln+bd */
    var seen={};var uRoster=[];
    roster.forEach(function(p){var k=p.fn+'|'+p.ln+'|'+(p.bd||'');if(!seen[k]){seen[k]=1;uRoster.push(p);}});
    var players=uRoster.filter(function(p){return p.ro==='player';});
    /* Sort players oldest first (birthdate ascending = oldest first) */
    players.sort(function(a,b){return(a.bd||'9999').localeCompare(b.bd||'9999');});
    var staff=uRoster.filter(function(p){return p.ro!=='player';});
    var rows='';
    players.forEach(function(p){
      var by='';
      if(p.bd){by=p.bd.substring(0,4);}
      var name=esc(titleCase(p.fn)+' '+titleCase(p.ln));
      rows+='<tr><td class="roster-name">'+name+'</td><td>'+by+'</td></tr>';
    });
    var srows='';
    staff.forEach(function(p){
      var name=esc(titleCase(p.fn)+' '+titleCase(p.ln));
      srows+='<tr><td class="roster-name">'+name+'</td><td class="roster-role">Staff</td></tr>';
    });
    rosH='<div class="section-block collapsed"><h3 onclick="toggleSection(this)">Plantilla ('+players.length+' jugadors)<span class="toggle-arrow">\u25B2</span></h3>'+
      '<div class="section-content"><div class="table-wrap"><table class="roster-table"><thead><tr><th>Nom</th><th>Any</th></tr></thead>'+
      '<tbody>'+rows+'</tbody></table></div>';
    if(srows)rosH+='<div class="roster-staff-title">Cos tecnic ('+staff.length+')</div>'+
      '<div class="table-wrap"><table class="roster-table"><thead><tr><th>Nom</th><th></th></tr></thead>'+
      '<tbody>'+srows+'</tbody></table></div>';
    rosH+='</div></div>';
  }
  document.getElementById('roster-'+entryId).innerHTML=rosH;

  /* Links */
  document.getElementById('links-'+entryId).innerHTML=
    '<a href="'+clupik+'/es/tournament/'+data.tid+'/summary" target="_blank" rel="noopener" class="btn-link">Veure competicio completa</a>'+
    '<a href="'+clupik+'/es/team/'+teamId+'" target="_blank" rel="noopener" class="btn-link">'+esc(teamName)+'</a>';
}

/* --- Init --- */
window.addEventListener('DOMContentLoaded',function(){
  var h=location.hash.slice(1);
  if(!h)return;
  if(h.startsWith('cat-')){showTeams(h.slice(4));}
  else{var el=document.getElementById(h);if(el&&el.classList.contains('detail-category'))showDetail(h);}
});
"""


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def generate_html(categories_data, config):
    clupik = config.get("clupik_base_url", CLUPIK_BASE)
    build_time = datetime.utcnow().strftime("%d/%m/%Y %H:%M UTC")

    # --- Explode categories into per-team entries ---
    entries = []
    for cat in categories_data:
        for team in cat["our_teams"]:
            team_id = team["id"]
            team_ids = {team_id}
            team_matches = [m for m in cat["matches"]
                           if m["home_team"] in team_ids or m["away_team"] in team_ids]
            team_groups = [g for g in cat["groups"] if team_id in g["our_team_ids"]]
            entries.append({
                "tournament_id": cat["tournament_id"],
                "tournament_name": cat["tournament_name"],
                "team": team,
                "team_ids": team_ids,
                "matches": team_matches,
                "all_groups": cat["groups"],         # ALL groups in tournament
                "all_matches": cat["matches"],        # ALL matches in tournament
                "our_groups": team_groups,             # groups where our team plays
                "team_names": cat["team_names"],
                "rosters": cat.get("rosters", {}),
            })

    # --- Group entries by tournament for 2-level nav ---
    from collections import OrderedDict
    tournaments_map = OrderedDict()
    for entry in entries:
        tid = entry["tournament_id"]
        if tid not in tournaments_map:
            tournaments_map[tid] = {
                "tournament_name": entry["tournament_name"],
                "entries": [],
            }
        tournaments_map[tid]["entries"].append(entry)

    # Sort tournaments by age (youngest first)
    sorted_tids = sorted(tournaments_map.keys(),
                         key=lambda tid: category_age_info(tournaments_map[tid]["tournament_name"])[0])
    sorted_map = OrderedDict((tid, tournaments_map[tid]) for tid in sorted_tids)
    tournaments_map = sorted_map

    # --- Screen 1: Category cards ---
    cat_card_items = []
    for tid, tinfo in tournaments_map.items():
        cat_id = slug(tinfo["tournament_name"])
        label = short_category(tinfo["tournament_name"])
        num_teams = len(tinfo["entries"])
        _, age_label = category_age_info(tinfo["tournament_name"])
        teams_str = " / ".join(escape(e["team"]["name"]) for e in tinfo["entries"])

        # Aggregate stats across all teams in this category
        total_past = sum(1 for e in tinfo["entries"] for m in e["matches"] if m["finished"])

        age_html = f'<div class="cat-card-age">{escape(age_label)}</div>' if age_label else ''
        cat_card_items.append(
            f'<div class="cat-card" onclick="showDetailOrTeams(\'{cat_id}\',{num_teams})">'
            f'<div class="cat-card-name">{escape(label)}</div>'
            f'{age_html}'
            f'<div class="cat-card-teams">{num_teams} equip{"s" if num_teams > 1 else ""}</div>'
            f'<div class="cat-card-record"><span class="gf">{total_past} partits jugats</span></div>'
            f'<span class="cat-card-arrow">&#8250;</span>'
            f'</div>'
        )

    # --- Screen 2: Team panels (one per category) ---
    team_panels = []
    for tid, tinfo in tournaments_map.items():
        cat_id = slug(tinfo["tournament_name"])
        label = short_category(tinfo["tournament_name"])
        team_cards = []
        for entry in tinfo["entries"]:
            team = entry["team"]
            team_ids = entry["team_ids"]
            entry_id = slug(entry["tournament_name"] + "-" + team["name"])
            team_name = escape(team["name"])

            past = [m for m in entry["matches"] if m["finished"]]
            future = [m for m in entry["matches"] if not m["finished"] and m["date"]]
            past.sort(key=lambda m: m["date"] or "", reverse=True)
            future.sort(key=lambda m: m["date"] or "")

            wins = sum(1 for m in past if match_result_class(m, team_ids) == "win")
            losses = sum(1 for m in past if match_result_class(m, team_ids) == "loss")
            draws = len(past) - wins - losses

            card_next = ""
            if future:
                nm = future[0]
                hn = escape(entry["team_names"].get(nm["home_team"], "?"))
                an = escape(entry["team_names"].get(nm["away_team"] or "", "Descansa"))
                card_next = (
                    f'<div class="cat-card-next">Proper: <strong>{format_date_short(nm["date"])}</strong> '
                    f'{hn} vs {an}</div>'
                )

            team_cards.append(
                f'<div class="cat-card" data-detail="{entry_id}" onclick="showDetail(\'{entry_id}\')">'
                f'<div class="cat-card-name">{team_name}</div>'
                f'<div class="cat-card-record">'
                f'<span class="w">{wins}V</span><span class="d">{draws}E</span>'
                f'<span class="l">{losses}D</span></div>'
                f'{card_next}'
                f'<span class="cat-card-arrow">&#8250;</span>'
                f'</div>'
            )

        team_panels.append(
            f'<div class="team-panel" id="teams-{cat_id}" style="display:none">'
            f'<div class="sel-title">{escape(label)}</div>'
            f'<div class="sel-subtitle">Selecciona equip</div>'
            f'<div class="cat-grid">{"".join(team_cards)}</div>'
            f'</div>'
        )

    # --- Screen 3: Build JSON data + detail shells ---
    import json as json_mod
    wp_data = {}
    global_rosters = {}  # team_id -> roster (deduplicated across entries)
    detail_sections = []
    for entry in entries:
        tid = entry["tournament_id"]
        team = entry["team"]
        team_ids = entry["team_ids"]
        cat_id = slug(entry["tournament_name"])
        entry_id = slug(entry["tournament_name"] + "-" + team["name"])
        num_teams = len(tournaments_map[tid]["entries"])

        # Build JSON for this entry – include ALL matches from ALL groups
        matches_json = []
        seen_match_ids = set()
        for m in entry["all_matches"]:
            if m["id"] in seen_match_ids:
                continue
            seen_match_ids.add(m["id"])
            hs_val, as_val = match_score(m)
            matches_json.append({
                "d": m["date"], "f": m["finished"],
                "h": m["home_team"], "a": m["away_team"],
                "hs": hs_val, "as": as_val,
                "rn": m.get("round_name", ""),
                "gn": m.get("group_name", ""),
                "v": m.get("venue", ""),
            })

        # Include ALL groups with standings
        groups_json = []
        all_team_ids_set = set()
        for g in entry["all_groups"]:
            standings_json = []
            for s in g["standings"]:
                all_team_ids_set.add(str(s["id"]))
                standings_json.append({
                    "id": str(s["id"]), "n": s["name"], "pos": s["position"],
                    "pts": s["points"], "pj": s["played"], "pg": s["won"],
                    "pe": s["drawn"], "pp": s["lost"], "gf": s["goals_for"],
                    "gc": s["goals_against"], "dg": s["goal_diff"],
                })
            groups_json.append({"id": g["id"], "n": g["name"], "s": standings_json})

        # Collect rosters into global dict (avoids duplication across entries)
        for t_id in all_team_ids_set | team_ids:
            if t_id not in global_rosters:
                roster = entry["rosters"].get(t_id, [])
                if roster:
                    global_rosters[t_id] = [{"fn": p["first_name"], "ln": p["last_name"],
                                              "bd": p.get("birthdate", ""), "ro": p["role"]}
                                             for p in roster]

        wp_data[entry_id] = {
            "tid": tid, "tname": entry["tournament_name"],
            "label": short_category(entry["tournament_name"]),
            "dt": team["id"],
            "teams": {k: v for k, v in entry["team_names"].items() if k in all_team_ids_set or k in team_ids},
            "groups": groups_json, "matches": matches_json,
        }

        # Build team selector options from ALL groups (sorted by standings position)
        team_options = []
        for g in entry["all_groups"]:
            for s in g["standings"]:
                sid = str(s["id"])
                if sid not in [t[0] for t in team_options]:
                    selected = " selected" if sid == team["id"] else ""
                    team_options.append((sid, f'<option value="{sid}"{selected}>{escape(s["name"])}</option>'))

        selector_html = "".join(t[1] for t in team_options)

        detail_section = (
            f'<div class="detail-category" id="{entry_id}" data-entry-id="{entry_id}" '
            f'data-cat-id="{cat_id}" data-num-teams="{num_teams}" '
            f'data-cat-label="{escape(short_category(entry["tournament_name"]))}" style="display:none">'
            f'<div class="category-header">'
            f'<h2>{escape(entry["tournament_name"])}</h2>'
            f'<div class="team-selector-wrap">'
            f'<label class="team-selector-label">Perspectiva equip:</label>'
            f'<select class="team-selector" onchange="renderForTeam(\'{entry_id}\',this.value)">'
            f'{selector_html}</select></div>'
            f'<div class="record-bar" id="record-{entry_id}"></div>'
            f'</div>'
            f'<div id="next-{entry_id}"></div>'
            f'<div class="section-block collapsed"><h3 onclick="toggleSection(this)">Classificacio<span class="toggle-arrow">\u25B2</span></h3><div class="section-content" id="standings-{entry_id}"></div></div>'
            f'<div class="section-block collapsed"><h3 onclick="toggleSection(this)">Resultats<span class="toggle-arrow">\u25B2</span></h3><div class="section-content" id="results-{entry_id}"></div></div>'
            f'<div id="upcoming-{entry_id}"></div>'
            f'<div id="roster-{entry_id}"></div>'
            f'<div class="section-block links-block" id="links-{entry_id}"></div>'
            f'</div>'
        )
        detail_sections.append(detail_section)

    # Serialize data for embedding
    wp_data_json = json_mod.dumps(wp_data, ensure_ascii=False, separators=(',', ':'))
    rosters_json = json_mod.dumps(global_rosters, ensure_ascii=False, separators=(',', ':'))

    total_cats = len(tournaments_map)

    html = (
        '<!DOCTYPE html><html lang="ca"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        f'<title>Waterpolo Tracker</title>'
        f'<style>{CSS}</style></head><body>'
        f'<header><div class="header-inner">'
        f'<div><h1>&#127937; Waterpolo Tracker</h1>'
        f'<div class="subtitle">{total_cats} categories</div>'
        f'</div></div></header>'
        f'<main>'
        # Screen 1: Categories
        f'<div id="selection-screen">'
        f'<div class="sel-title">Selecciona una categoria</div>'
        f'<div class="search-wrap">'
        f'<span class="search-icon">&#128269;</span>'
        f'<input type="text" id="search-input" class="search-input" placeholder="Buscar jugador o staff..." oninput="doSearch(this.value)" autocomplete="off">'
        f'<button id="search-clear" class="search-clear" onclick="clearSearch()">&times;</button>'
        f'<div id="search-results" class="search-results" style="display:none"></div>'
        f'</div>'
        f'<div class="cat-grid">{"".join(cat_card_items)}</div>'
        f'</div>'
        # Screen 2: Team selection
        f'<div id="team-screen" style="display:none">'
        f'<div class="back-bar"><button class="btn-back" onclick="showCategories()">&#8249; Tornar</button>'
        f'<span class="back-label">Totes les categories</span></div>'
        f'{"".join(team_panels)}'
        f'</div>'
        # Screen 3: Detail
        f'<div id="detail-screen" style="display:none">'
        f'<div class="back-bar" id="detail-back-bar"><button class="btn-back" id="detail-back-btn">&#8249; Tornar</button>'
        f'<span class="back-label" id="detail-back-label"></span></div>'
        f'{"".join(detail_sections)}'
        f'</div>'
        f'</main>'
        f'<footer>Actualitzat: {build_time}<br>'
        'Dades de <a href="https://actawp.natacio.cat/">Federacio Catalana de Natacio</a> '
        'via <a href="https://clupik.pro">Clupik</a> (API Leverade)<br>'
        'Generat automaticament - <a href="https://github.com/vinner21/water_follow">GitHub</a></footer>'
        f'<script>window.WP={wp_data_json};window.ROST={rosters_json};window.CLUPIK="{clupik}";</script>'
        f'<script>{JS}</script></body></html>'
    )
    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path) as f:
        config = json.load(f)

    club_id = config["club_id"]
    manager_id = config["manager_id"]

    tournaments = discover_tournaments(manager_id, club_id)
    if not tournaments:
        print("No tournaments found for this club.")
        sys.exit(1)

    categories_data = []
    for t in tournaments:
        print(f"\nCollecting data for: {t['name']}")
        try:
            cat_data = collect_tournament_data(t, club_id)
            if cat_data["groups"]:
                categories_data.append(cat_data)
                print(f"  -> {len(cat_data['matches'])} matches, {len(cat_data['groups'])} group(s)")
            else:
                print(f"  -> No groups with our teams found, skipping")
        except Exception as e:
            print(f"  -> ERROR: {e}")
            continue

    html = generate_html(categories_data, config)
    out_dir = os.path.join(os.path.dirname(__file__), "_site")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Write robots.txt to block crawlers
    robots_path = os.path.join(out_dir, "robots.txt")
    with open(robots_path, "w") as f:
        f.write("User-agent: *\nDisallow: /\n")
    print(f"robots.txt generated")

    # Encrypt with StatiCrypt
    import subprocess
    import shutil
    staticrypt_bin = shutil.which("staticrypt")
    if staticrypt_bin:
        print("Encrypting with StatiCrypt ...")
        result = subprocess.run([
            staticrypt_bin, out_path,
            "-p", os.environ.get("STATICRYPT_PASSWORD", "vidalperez"),
            "--short",
            "--remember", "30",
            "--template-title", "Water Polo Tracker - Login",
            "--template-instructions", "Introdueix la contrasenya per accedir.",
            "--template-button", "Entrar",
            "--template-placeholder", "Contrasenya",
            "--template-remember", "Recorda'm 30 dies",
            "--template-error", "Contrasenya incorrecta!",
            "--template-color-primary", "#0077B6",
            "--template-color-secondary", "#023E8A",
            "-d", out_dir,
        ], capture_output=True, text=True)
        if result.returncode == 0:
            print("  Encrypted successfully")
        else:
            print(f"  StatiCrypt error: {result.stderr}")
    else:
        print("WARNING: staticrypt not found, HTML NOT encrypted")

    print(f"\n{'='*60}")
    print(f"Site generated: {out_path}")
    print(f"Categories: {len(categories_data)}")
    total_matches = sum(len(c['matches']) for c in categories_data)
    print(f"Total matches: {total_matches}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
