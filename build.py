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
    data = api_get(f"rounds/{rid}", params={"include": "matches.results"})
    results_map = {}
    matches = []
    for inc in data.get("included", []):
        if inc["type"] == "result":
            results_map[inc["id"]] = {
                "value": inc["attributes"]["value"],
                "score": inc["attributes"]["score"],
                "team_id": inc["relationships"]["team"]["data"]["id"],
                "match_id": inc["relationships"]["match"]["data"]["id"],
            }
        elif inc["type"] == "match":
            meta = inc.get("meta", {})
            match = {
                "id": inc["id"], "date": inc["attributes"]["date"],
                "finished": inc["attributes"]["finished"],
                "canceled": inc["attributes"]["canceled"],
                "postponed": inc["attributes"]["postponed"],
                "rest": inc["attributes"].get("rest", False),
                "home_team": meta.get("home_team"),
                "away_team": meta.get("away_team"),
                "results": [],
            }
            for res_ref in inc.get("relationships", {}).get("results", {}).get("data", []):
                r = results_map.get(res_ref["id"])
                if r:
                    match["results"].append(r)
            matches.append(match)
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
        print(f"    Checking standings for {g['name']} ...", end=" ")
        standings = get_standings(gid)
        standing_team_ids = set()
        for row in standings:
            team_names[str(row["id"])] = row["name"]
            standing_team_ids.add(str(row["id"]))
        our_in_group = our_team_ids & standing_team_ids
        if not our_in_group:
            print("-")
            continue
        print(f"OK ({len(our_in_group)} team(s))")

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

        our_matches = [m for m in group_matches
                       if m["home_team"] in our_team_ids or m["away_team"] in our_team_ids]
        collected_groups.append({
            "id": gid, "name": g["name"],
            "standings": standings, "our_team_ids": our_in_group,
        })
        all_matches.extend(our_matches)

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
    return {
        "tournament_id": tid, "tournament_name": tournament["name"],
        "our_teams": tournament["our_teams"], "our_team_ids": our_team_ids,
        "groups": collected_groups, "matches": all_matches, "team_names": team_names,
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
    name = name.replace("LLIGA CATALANA ", "").replace("COMPETICIO CATALANA ", "")
    for old, new in [("MASCULINA DE PROMOCIO", "Promo Masc."), ("MASCULI", "Masc."),
                     ("MASCULINA", "Masc."), ("FEMENI", "Fem."), ("FEMENINA", "Fem."),
                     ("MIXTE", "Mixt"), ("MIXTA", "Mixt"), ("BENJAMINA", "Benjami"),
                     ("MASTER", "Master")]:
        name = name.replace(old, new)
    return name.strip()


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
.sel-title{text-align:center;font-size:1rem;color:var(--blue-dark);margin:.8rem 0 .6rem;font-weight:600}
.cat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:.6rem;padding:0 .2rem}
.cat-card{background:var(--card);border-radius:var(--radius);padding:.8rem;cursor:pointer;border:2px solid transparent;transition:border-color .2s,box-shadow .2s,transform .15s;position:relative;overflow:hidden}
.cat-card:hover{border-color:var(--blue-light);box-shadow:0 4px 12px rgba(0,119,182,.15);transform:translateY(-2px)}
.cat-card-name{font-size:.85rem;font-weight:700;color:var(--blue-dark);margin-bottom:.3rem}
.cat-card-teams{font-size:.75rem;color:var(--text-muted);margin-bottom:.35rem}
.cat-card-record{display:inline-flex;gap:.3rem;font-size:.7rem}
.cat-card-record span{padding:.1rem .35rem;border-radius:3px;font-weight:600}
.cat-card-record .w{background:#d4edda;color:var(--green)}.cat-card-record .d{background:#fff3cd;color:#856404}
.cat-card-record .l{background:#f8d7da;color:var(--red)}
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
.teams-label{font-size:.8rem;color:var(--text-muted);margin-bottom:.4rem}
.record-bar{display:inline-flex;gap:.4rem;font-size:.78rem}
.record-bar span{padding:.12rem .45rem;border-radius:4px;font-weight:600}
.record-bar .w{background:#d4edda;color:var(--green)}.record-bar .d{background:#fff3cd;color:#856404}
.record-bar .l{background:#f8d7da;color:var(--red)}.record-bar .gf{background:var(--blue-pale);color:var(--blue-dark)}
.record-bar .ga{background:#e9ecef;color:var(--text-muted)}
.section-block{background:var(--card);border-radius:var(--radius);padding:.8rem;margin-bottom:.6rem}
.section-block h3{font-size:.9rem;color:var(--blue-dark);border-bottom:2px solid var(--blue-pale);padding-bottom:.25rem;margin-bottom:.5rem}
.empty{color:var(--text-muted);font-size:.85rem}
.next-match-card{background:linear-gradient(135deg,var(--blue),var(--blue-dark));color:#fff;border-radius:8px;padding:1rem;text-align:center}
.next-date{font-size:.85rem;opacity:.85;margin-bottom:.35rem}
.next-teams{font-size:1.2rem;font-weight:700}
.next-teams .vs{margin:0 .4rem;opacity:.6;font-weight:400;font-size:.9rem}
.next-round{font-size:.75rem;opacity:.65;margin-top:.2rem}
.our-team{color:var(--blue-light)}.next-match-card .our-team{color:#ffd166}
.match-row{display:grid;grid-template-columns:64px 68px 1fr;align-items:center;gap:.4rem;padding:.5rem .6rem;border-radius:6px;margin-bottom:.25rem;border-left:4px solid transparent;background:var(--bg);transition:box-shadow .15s}
.match-row:hover{box-shadow:0 1px 6px rgba(0,0,0,.06)}
.match-row.win{border-left-color:var(--green)}.match-row.loss{border-left-color:var(--red)}
.match-row.draw{border-left-color:var(--orange)}.match-row.upcoming{border-left-color:var(--blue-light);background:var(--blue-pale)}
.match-date{font-size:.72rem;color:var(--text-muted)}.match-round{font-size:.68rem;color:var(--text-muted)}
.match-teams{font-size:.8rem;display:flex;align-items:center;gap:.25rem;flex-wrap:wrap}
.score{font-weight:700;min-width:1.1rem;text-align:center}.score-sep{color:var(--text-muted)}
.vs-small{color:var(--text-muted);font-size:.72rem;margin:0 .2rem}
.standings-block{margin-bottom:.6rem}
.standings-block h3{font-size:.82rem;color:var(--blue);margin-bottom:.3rem;border:none;padding:0}
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
footer{text-align:center;padding:1.2rem 1rem;font-size:.72rem;color:var(--text-muted)}
footer a{color:var(--blue)}
@media(max-width:480px){.cat-grid{grid-template-columns:1fr}.match-row{grid-template-columns:52px 56px 1fr;padding:.4rem}.match-teams{font-size:.74rem}header h1{font-size:1.1rem}}
"""

JS = """
function showDetail(id){
  document.getElementById('selection-screen').style.display='none';
  document.getElementById('detail-screen').style.display='block';
  document.querySelectorAll('.detail-category').forEach(c=>c.style.display='none');
  const el=document.getElementById(id);
  if(el)el.style.display='block';
  window.scrollTo(0,0);
  history.replaceState(null,'','#'+id);
}
function showSelection(){
  document.getElementById('detail-screen').style.display='none';
  document.getElementById('selection-screen').style.display='block';
  window.scrollTo(0,0);
  history.replaceState(null,'','#');
}
window.addEventListener('DOMContentLoaded',()=>{
  const h=location.hash.slice(1);
  if(h){const el=document.getElementById(h);if(el&&el.classList.contains('detail-category'))showDetail(h);}
});
"""


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def generate_html(categories_data, config):
    club_name = escape(config["club_name"])
    clupik = config.get("clupik_base_url", CLUPIK_BASE)

    club_avatar = ""
    for cat in categories_data:
        for t in cat["our_teams"]:
            if t.get("avatar"):
                club_avatar = t["avatar"]
                break
        if club_avatar:
            break

    build_time = datetime.utcnow().strftime("%d/%m/%Y %H:%M UTC")

    # --- Build selection cards ---
    card_items = []
    detail_sections = []

    for idx, cat in enumerate(categories_data):
        tid = cat["tournament_id"]
        cat_id = slug(cat["tournament_name"])
        label = short_category(cat["tournament_name"])
        our_ids = cat["our_team_ids"]

        past = [m for m in cat["matches"] if m["finished"]]
        future = [m for m in cat["matches"] if not m["finished"] and m["date"]]
        past.sort(key=lambda m: m["date"] or "", reverse=True)
        future.sort(key=lambda m: m["date"] or "")

        wins = sum(1 for m in past if match_result_class(m, our_ids) == "win")
        losses = sum(1 for m in past if match_result_class(m, our_ids) == "loss")
        draws = len(past) - wins - losses
        gf = ga = 0
        for m in past:
            hs, aws = match_score(m)
            if hs is not None and aws is not None:
                if m["home_team"] in our_ids:
                    gf += hs; ga += aws
                else:
                    gf += aws; ga += hs

        our_teams_str = " / ".join(escape(t["name"]) for t in cat["our_teams"])

        # Card: next match preview
        card_next = ""
        if future:
            nm = future[0]
            hn = escape(cat["team_names"].get(nm["home_team"], "?"))
            an = escape(cat["team_names"].get(nm["away_team"] or "", "Descansa"))
            card_next = (
                f'<div class="cat-card-next">Proper: <strong>{format_date_short(nm["date"])}</strong> '
                f'{hn} vs {an}</div>'
            )

        card_items.append(
            f'<div class="cat-card" onclick="showDetail(\'{cat_id}\')">'
            f'<div class="cat-card-name">{escape(label)}</div>'
            f'<div class="cat-card-teams">{our_teams_str}</div>'
            f'<div class="cat-card-record">'
            f'<span class="w">{wins}V</span><span class="d">{draws}E</span>'
            f'<span class="l">{losses}D</span></div>'
            f'{card_next}'
            f'<span class="cat-card-arrow">&#8250;</span>'
            f'</div>'
        )

        # --- Build detail section for this category ---
        # Next match
        next_match_html = ""
        if future:
            nm = future[0]
            hn = escape(cat["team_names"].get(nm["home_team"], "?"))
            an = escape(cat["team_names"].get(nm["away_team"] or "", "Descansa"))
            is_home = nm["home_team"] in our_ids
            next_match_html = (
                '<div class="next-match-card">'
                f'<div class="next-date">{format_date(nm["date"])}</div>'
                '<div class="next-teams">'
                f'<span class="{"our-team" if is_home else ""}">{hn}</span>'
                '<span class="vs">vs</span>'
                f'<span class="{"our-team" if not is_home else ""}">{an}</span>'
                '</div>'
                f'<div class="next-round">{escape(nm.get("round_name", ""))}</div>'
                '</div>'
            )

        # Results
        results_items = []
        for m in past:
            hn = escape(cat["team_names"].get(m["home_team"], "?"))
            an = escape(cat["team_names"].get(m["away_team"] or "", "Descansa"))
            hs, aws = match_score(m)
            rcls = match_result_class(m, our_ids)
            ds = format_date_short(m["date"])
            is_home = m["home_team"] in our_ids
            results_items.append(
                f'<div class="match-row {rcls}">'
                f'<span class="match-date">{ds}</span>'
                f'<span class="match-round">{escape(m.get("round_name", ""))}</span>'
                f'<span class="match-teams">'
                f'<span class="{"our-team" if is_home else ""}">{hn}</span>'
                f'<span class="score">{hs if hs is not None else "-"}</span>'
                f'<span class="score-sep">-</span>'
                f'<span class="score">{aws if aws is not None else "-"}</span>'
                f'<span class="{"our-team" if not is_home else ""}">{an}</span>'
                f'</span></div>'
            )

        # Upcoming
        upcoming_items = []
        for m in future[1:]:
            hn = escape(cat["team_names"].get(m["home_team"], "?"))
            an = escape(cat["team_names"].get(m["away_team"] or "", "Descansa"))
            ds = format_date_short(m["date"])
            is_home = m["home_team"] in our_ids
            upcoming_items.append(
                f'<div class="match-row upcoming">'
                f'<span class="match-date">{ds}</span>'
                f'<span class="match-round">{escape(m.get("round_name", ""))}</span>'
                f'<span class="match-teams">'
                f'<span class="{"our-team" if is_home else ""}">{hn}</span>'
                f'<span class="vs-small">vs</span>'
                f'<span class="{"our-team" if not is_home else ""}">{an}</span>'
                f'</span></div>'
            )

        # Standings
        standings_html = ""
        for grp in cat["groups"]:
            rows = ""
            for s in grp["standings"]:
                is_ours = str(s["id"]) in our_ids
                cls = ' class="highlight"' if is_ours else ""
                rows += (
                    f'<tr{cls}>'
                    f'<td class="pos">{s["position"]}</td>'
                    f'<td class="team-name-cell">{escape(s["name"])}</td>'
                    f'<td>{s["played"]}</td><td>{s["won"]}</td>'
                    f'<td>{s["drawn"]}</td><td>{s["lost"]}</td>'
                    f'<td>{s["goals_for"]}</td><td>{s["goals_against"]}</td>'
                    f'<td>{s["goal_diff"]:+d}</td>'
                    f'<td class="pts">{s["points"]}</td></tr>'
                )
            standings_html += (
                f'<div class="standings-block"><h3>{escape(grp["name"])}</h3>'
                '<div class="table-wrap"><table><thead><tr>'
                '<th>#</th><th>Equip</th><th>PJ</th><th>PG</th><th>PE</th>'
                '<th>PP</th><th>GF</th><th>GC</th><th>DG</th><th>Pts</th>'
                f'</tr></thead><tbody>{rows}</tbody></table></div></div>'
            )

        team_links = "".join(
            f'<a href="{clupik}/es/team/{t["id"]}" target="_blank" rel="noopener" class="btn-link">{escape(t["name"])}</a>'
            for t in cat["our_teams"]
        )
        tournament_link = f'<a href="{clupik}/es/tournament/{tid}/summary" target="_blank" rel="noopener" class="btn-link">Veure competicio completa</a>'

        results_block = "\n".join(results_items) if results_items else '<p class="empty">Encara no hi ha resultats.</p>'
        next_block = f'<div class="section-block"><h3>Proper Partit</h3>{next_match_html}</div>' if next_match_html else ""
        upcoming_block = f'<div class="section-block"><h3>Propers Partits</h3>{"".join(upcoming_items)}</div>' if upcoming_items else ""
        standings_block = standings_html if standings_html else '<p class="empty">Classificacio no disponible.</p>'

        detail_section = (
            f'<div class="detail-category" id="{cat_id}" style="display:none">'
            f'<div class="category-header"><h2>{escape(cat["tournament_name"])}</h2>'
            f'<div class="teams-label">{our_teams_str}</div>'
            f'<div class="record-bar">'
            f'<span class="w">{wins}V</span><span class="d">{draws}E</span>'
            f'<span class="l">{losses}D</span><span class="gf">{gf}GF</span>'
            f'<span class="ga">{ga}GC</span></div></div>'
            f'{next_block}'
            f'<div class="section-block"><h3>Resultats</h3>{results_block}</div>'
            f'{upcoming_block}'
            f'<div class="section-block"><h3>Classificacio</h3>{standings_block}</div>'
            f'<div class="section-block links-block">{tournament_link}{team_links}</div>'
            '</div>'
        )
        detail_sections.append(detail_section)

    total_cats = len(categories_data)
    total_teams = sum(len(c["our_teams"]) for c in categories_data)

    avatar_tag = f'<img src="{escape(club_avatar)}" alt="" class="club-logo">' if club_avatar else ""

    html = (
        '<!DOCTYPE html><html lang="ca"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{club_name} - Waterpolo Tracker</title>'
        f'<style>{CSS}</style></head><body>'
        f'<header><div class="header-inner">{avatar_tag}'
        f'<div><h1>{club_name}</h1>'
        f'<div class="subtitle">Seguiment Waterpolo &middot; {total_cats} categories &middot; {total_teams} equips</div>'
        f'</div></div></header>'
        f'<main>'
        f'<div id="selection-screen">'
        f'<div class="sel-title">Selecciona una categoria</div>'
        f'<div class="cat-grid">{"".join(card_items)}</div>'
        f'</div>'
        f'<div id="detail-screen">'
        f'<div class="back-bar"><button class="btn-back" onclick="showSelection()">&#8249; Tornar</button>'
        f'<span class="back-label">Totes les categories</span></div>'
        f'{"".join(detail_sections)}'
        f'</div>'
        f'</main>'
        f'<footer>Actualitzat: {build_time}<br>'
        'Dades de <a href="https://actawp.natacio.cat/">Federacio Catalana de Natacio</a> '
        'via <a href="https://clupik.pro">Clupik</a> (API Leverade)<br>'
        'Generat automaticament - <a href="https://github.com/vinner21/water_follow">GitHub</a></footer>'
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

    print(f"\n{'='*60}")
    print(f"Site generated: {out_path}")
    print(f"Categories: {len(categories_data)}")
    total_matches = sum(len(c['matches']) for c in categories_data)
    print(f"Total matches: {total_matches}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
