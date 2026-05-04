#!/usr/bin/env python3
"""
Build script for Water Polo Tracker - Multi-Category, Multi-Season.

Fetches data from the Leverade API (used by clupik.pro / Federacio Catalana
de Natacio) and generates a static HTML site with all water-polo categories
where the configured club has teams.

Supports historical seasons: finished seasons are cached as JSON files in
_data/seasons/ so API calls are only made once per closed season.
"""

import json
import os
import re
import sys
import time
import unicodedata
from collections import OrderedDict
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None
from html import escape

import requests

API_BASE = "https://api.leverade.com"
CLUPIK_BASE = "https://clupik.pro"
REQUEST_DELAY = 0.3
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_data", "seasons")


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
# Season cache
# ---------------------------------------------------------------------------

def load_season_cache(season_id):
    """Load cached season data from _data/seasons/{season_id}.json.
    Returns None if cache file does not exist."""
    path = os.path.join(DATA_DIR, f"{season_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # Convert lists back to sets where needed
    for t in data.get("tournaments", []):
        _deserialize_category(t)
    print(f"  Loaded season {data.get('season_label', season_id)} from cache ({path})")
    return data


def _serialize_category(cat):
    """Convert a single category/tournament data dict to a JSON-serializable form."""
    teams = cat.get("teams") or cat.get("our_teams") or []
    team_ids = cat.get("team_ids") or cat.get("our_team_ids") or set()
    c = {
        "tournament_id": cat["tournament_id"],
        "tournament_name": cat["tournament_name"],
        "teams": teams,
        "team_ids": list(team_ids),
        "matches": cat["matches"],
        "team_names": cat["team_names"],
        "rosters": cat.get("rosters", {}),
        "groups": [],
    }
    for g in cat["groups"]:
        c["groups"].append({
            "id": g["id"],
            "name": g["name"],
            "standings": g["standings"],
            "team_ids": list(g.get("team_ids") or g.get("our_team_ids") or set()),
        })
    return c


def _deserialize_category(cat):
    """Restore sets from lists after loading from JSON."""
    if "team_ids" in cat:
        cat["team_ids"] = set(cat["team_ids"])
    else:
        cat["team_ids"] = set(cat.get("our_team_ids", []))
    if "teams" not in cat:
        cat["teams"] = cat.get("our_teams", [])
    for g in cat.get("groups", []):
        if "team_ids" in g:
            g["team_ids"] = set(g["team_ids"])
        else:
            g["team_ids"] = set(g.get("our_team_ids", []))
    return cat


def save_season_cache(season_id, season_label, categories_data):
    """Persist finished-season data as JSON so it never needs to be fetched again."""
    os.makedirs(DATA_DIR, exist_ok=True)
    serializable = [_serialize_category(cat) for cat in categories_data]
    payload = {
        "season_id": season_id,
        "season_label": season_label,
        "tournaments": serializable,
        "refreshed_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
    }
    path = os.path.join(DATA_DIR, f"{season_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"  Cached season {season_label} -> {path}")


# ---------------------------------------------------------------------------
# Tournament-level cache (for finished tournaments within current season)
# ---------------------------------------------------------------------------

def load_tournament_cache(tournament_id):
    """Load cached tournament data from _data/seasons/t_{tournament_id}.json.
    Returns the deserialized category dict, or None."""
    path = os.path.join(DATA_DIR, f"t_{tournament_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return _deserialize_category(data)


def save_tournament_cache(tournament_id, cat_data):
    """Cache a single finished tournament's collected data."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"t_{tournament_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_serialize_category(cat_data), f, ensure_ascii=False, indent=1)
    print(f"    Cached tournament {tournament_id} -> {path}")


def cleanup_tournament_caches():
    """Remove per-tournament cache files (used after a season is fully cached)."""
    if not os.path.isdir(DATA_DIR):
        return
    for fname in os.listdir(DATA_DIR):
        if fname.startswith("t_") and fname.endswith(".json"):
            os.remove(os.path.join(DATA_DIR, fname))
            print(f"  Cleaned up tournament cache: {fname}")


# ---------------------------------------------------------------------------
# Roster cache  (r_{team_id}.json)  – refreshed only with --refresh-rosters
# ---------------------------------------------------------------------------

ROSTER_DIR = os.path.join(DATA_DIR, "rosters")


def load_roster_cache(team_id):
    """Load a cached roster for a single team.  Returns list or None."""
    path = os.path.join(ROSTER_DIR, f"r_{team_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_roster_cache(team_id, roster):
    """Persist a single team's roster to disk."""
    os.makedirs(ROSTER_DIR, exist_ok=True)
    path = os.path.join(ROSTER_DIR, f"r_{team_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(roster, f, ensure_ascii=False, indent=1)


def load_all_roster_caches(team_ids):
    """Load cached rosters for a set of team_ids.
    Returns (dict of rosters found, set of missing ids)."""
    rosters = {}
    missing = set()
    for t_id in team_ids:
        cached = load_roster_cache(t_id)
        if cached is not None:
            rosters[t_id] = cached
        else:
            missing.add(t_id)
    return rosters, missing


def roster_cache_age_days(team_id):
    """Return the age in days of the roster cache file, or None if it doesn't exist."""
    path = os.path.join(ROSTER_DIR, f"r_{team_id}.json")
    if not os.path.exists(path):
        return None
    mtime = os.path.getmtime(path)
    return (time.time() - mtime) / 86400


# ---------------------------------------------------------------------------
# Season helpers
# ---------------------------------------------------------------------------

def infer_season_info(categories_data):
    """Infer (season_label, season_start_year) from tournament match dates.
    Returns e.g. ('2024-25', 2024)."""
    all_dates = []
    for cat in categories_data:
        for m in cat.get("matches", []):
            d = m.get("date")
            if d:
                try:
                    dt = datetime.strptime(d, "%Y-%m-%d %H:%M:%S")
                    if ZoneInfo:
                        try:
                            dt = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Europe/Madrid"))
                        except Exception:
                            pass
                    all_dates.append(dt)
                except (ValueError, TypeError):
                    pass
    if all_dates:
        earliest = min(all_dates)
        start_year = earliest.year if earliest.month >= 7 else earliest.year - 1
        return f"{start_year}-{(start_year + 1) % 100:02d}", start_year
    # Fallback: try to extract from tournament names
    for cat in categories_data:
        name = cat.get("tournament_name", "")
        match = re.search(r"(\d{4})[/-](\d{2,4})", name)
        if match:
            year = int(match.group(1))
            return f"{year}-{(year + 1) % 100:02d}", year
    # Last resort: current year
    year = datetime.now().year
    return f"{year}-{(year + 1) % 100:02d}", year


def build_category_age(season_start_year):
    """Build age-category labels for a specific season.

    Catalan water polo age categories are based on birth year.
    season_start_year: e.g. 2025 for the 2025-26 season.
    """
    y = season_start_year
    return {
        "BENJAMI":  (1, f"9-10 anys ({y-10}-{(y-9) % 100:02d})"),
        "ALEVI":    (2, f"11-12 anys ({y-12}-{(y-11) % 100:02d})"),
        "INFANTIL": (3, f"13-14 anys ({y-14}-{(y-13) % 100:02d})"),
        "CADET":    (4, f"15-16 anys ({y-16}-{(y-15) % 100:02d})"),
        "JUVENIL":  (5, f"17-18 anys ({y-18}-{(y-17) % 100:02d})"),
        "ABSOLUTA": (6, "+18 anys"),
        "MASTER":   (7, "+30 anys"),
    }


# Default categories (season 2025-2026) – used as fallback
CATEGORY_AGE = build_category_age(2025)


def category_age_info(tournament_name, category_age=None):
    """Return (sort_order, age_label) for a tournament name."""
    if category_age is None:
        category_age = CATEGORY_AGE
    upper = tournament_name.upper()
    for key, (order, label) in category_age.items():
        if key in upper:
            return order, label
    return 99, ""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_tournaments(manager_id, club_id):
    """Original single-season discovery – kept for backwards compatibility."""
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


def discover_seasons(manager_id):
    """Discover ALL seasons from the manager endpoint.

    Returns dict of season_id -> {tournaments: [...], has_in_progress: bool}
    where each tournament has {id, name, gender, order, season_id, api_status}.
    """
    print("Fetching manager tournaments (all seasons) ...")
    data = api_get(f"managers/{manager_id}", params={"include": "tournaments"})
    seasons = {}
    for inc in data.get("included", []):
        if inc["type"] != "tournament":
            continue
        attrs = inc["attributes"]
        status = attrs["status"]
        if status not in ("in_progress", "finished"):
            continue
        season_data = inc["relationships"].get("season", {}).get("data")
        sid = season_data["id"] if season_data else "unknown"
        if sid not in seasons:
            seasons[sid] = {"tournaments": [], "has_in_progress": False}
        seasons[sid]["tournaments"].append({
            "id": inc["id"], "name": attrs["name"],
            "gender": attrs.get("gender"), "order": attrs.get("order"),
            "season_id": sid, "api_status": status,
        })
        if status == "in_progress":
            seasons[sid]["has_in_progress"] = True
    total = sum(len(s["tournaments"]) for s in seasons.values())
    print(f"  Found {total} tournaments across {len(seasons)} seasons")
    return seasons


def discover_club_tournaments(tournaments, club_id=None):
    """For a list of tournaments, load all teams and their clubs.

    club_id is kept for backward compatibility but ignored in multi-club mode.
    """
    result = []
    for t in tournaments:
        print(f"    Checking {t['name']} ...", end=" ")
        try:
            tdata = api_get(f"tournaments/{t['id']}", params={"include": "teams,teams.club"})
        except Exception as e:
            print(f"SKIP ({e})")
            continue

        clubs_by_id = {}
        for inc in tdata.get("included", []):
            if inc.get("type") == "club":
                clubs_by_id[inc["id"]] = inc.get("attributes", {}).get("name", f"Club {inc['id']}")

        all_teams = []
        for inc in tdata.get("included", []):
            if inc["type"] != "team":
                continue
            club_data = inc.get("relationships", {}).get("club", {}).get("data", {})
            club_ref_id = club_data.get("id") if club_data else None
            inferred = infer_club_from_team_name(inc["attributes"]["name"])
            avatar = inc.get("meta", {}).get("avatar", {}).get("large", "")
            all_teams.append({
                "id": inc["id"],
                "name": inc["attributes"]["name"],
                "avatar": avatar,
                "club_id": club_ref_id or inferred["club_id"],
                "club_name": clubs_by_id.get(club_ref_id) or inferred["club_name"],
            })

        if all_teams:
            t["teams"] = all_teams
            result.append(t)
            print(f"OK ({len(all_teams)} equips)")
        else:
            print("-")
    result.sort(key=lambda t: t.get("order") or 999)
    return result


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


def collect_tournament_data(tournament, club_id=None, refresh_rosters=False, is_current_season=False):
    tid = tournament["id"]
    tournament_team_ids = {t["id"] for t in (tournament.get("teams") or [])}
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
        team_in_group = tournament_team_ids & standing_team_ids

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
            "standings": standings, "team_ids": team_in_group,
        })
        all_matches.extend(group_matches)
        print(f"{len(group_matches)} matches")

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
    for t in (tournament.get("teams") or []):
        team_names[t["id"]] = t["name"]

    # Normalize match datetimes: add timezone-aware ISO string and epoch ts
    for m in all_matches:
        dt = parse_api_date(m.get("date"))
        if dt:
            try:
                m["date_local"] = dt.isoformat()
            except Exception:
                m["date_local"] = None
            try:
                m["date_ts"] = int(dt.timestamp())
            except Exception:
                m["date_ts"] = None
        else:
            m["date_local"] = None
            m["date_ts"] = None

    all_matches.sort(key=lambda m: m.get("date_ts") or 9999999999)

    # Roster handling: use cache unless --refresh-rosters was passed
    all_team_ids_in_groups = set()
    for g in collected_groups:
        for row in g["standings"]:
            all_team_ids_in_groups.add(str(row["id"]))
    rosters = {}
    if refresh_rosters:
        print(f"    Fetching rosters for {len(all_team_ids_in_groups)} teams (refresh mode) ...")
        for t_id in sorted(all_team_ids_in_groups):
            try:
                rosters[t_id] = get_team_roster(t_id)
                save_roster_cache(t_id, rosters[t_id])
            except Exception as e:
                print(f"      Warning: could not fetch roster for {t_id}: {e}")
                rosters[t_id] = []
        print(f"    Rosters: {sum(len(r) for r in rosters.values())} total participants")
    else:
        cached, missing = load_all_roster_caches(all_team_ids_in_groups)
        rosters = dict(cached)

        if is_current_season:
            # Auto-refresh ALL teams if cache is missing or older than 30 days (1 month)
            teams_to_fetch = set()
            for t_id in all_team_ids_in_groups:
                age = roster_cache_age_days(t_id)
                if age is None or age > 30:
                    teams_to_fetch.add(t_id)
            if teams_to_fetch:
                print(f"    Auto-refreshing rosters for {len(teams_to_fetch)} teams "
                      f"(missing or >30 days old) ...")
                for t_id in sorted(teams_to_fetch):
                    try:
                        roster = get_team_roster(t_id)
                        rosters[t_id] = roster
                        save_roster_cache(t_id, roster)
                        print(f"      Fetched roster for team {t_id}: {len(roster)} participants")
                    except Exception as e:
                        print(f"      Warning: could not fetch roster for {t_id}: {e}")
                        rosters[t_id] = cached.get(t_id, [])
            else:
                print(f"    All team rosters are fresh (cached <30 days)")

        for m_id in missing:
            if m_id not in rosters:
                rosters[m_id] = []  # empty until next --refresh-rosters run
        if cached:
            print(f"    Rosters: loaded {len(cached)} from cache ({sum(len(r) for r in cached.values())} participants)")
        remaining_missing = [m_id for m_id in missing if not rosters.get(m_id)]
        if remaining_missing:
            print(f"    Rosters: {len(remaining_missing)} teams without cache (run with --refresh-rosters)")

    return {
        "tournament_id": tid, "tournament_name": tournament["name"],
        "teams": tournament.get("teams", []), "team_ids": tournament_team_ids,
        "groups": collected_groups, "matches": all_matches, "team_names": team_names,
        "rosters": rosters,
    }


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def format_date(date_str):
    if not date_str:
        return "Per determinar"
    dt = parse_api_date(date_str)
    if not dt:
        return "Per determinar"
    days_ca = ["Dl", "Dt", "Dc", "Dj", "Dv", "Ds", "Dg"]
    return f"{days_ca[dt.weekday()]} {dt.day:02d}/{dt.month:02d}/{dt.year} {dt.hour:02d}:{dt.minute:02d}"


def format_date_short(date_str):
    if not date_str:
        return "TBD"
    dt = parse_api_date(date_str)
    if not dt:
        return "TBD"
    return f"{dt.day:02d}/{dt.month:02d} {dt.hour:02d}:{dt.minute:02d}"


def parse_api_date(date_str):
    """Parse an API date string (YYYY-MM-DD HH:MM:SS) and return a timezone-aware
    datetime in Europe/Madrid when possible. Returns None on parse error.
    """
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
    # Interpret API naive timestamps as UTC and convert to Europe/Madrid
    if ZoneInfo:
        try:
            dt = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Europe/Madrid"))
        except Exception:
            pass
    return dt


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


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _club_slug(name):
    txt = (name or "").strip().lower()
    txt = unicodedata.normalize("NFD", txt)
    txt = "".join(ch for ch in txt if unicodedata.category(ch) != "Mn")
    txt = re.sub(r"[^a-z0-9]+", "-", txt).strip("-")
    return txt or "club-unknown"


def _club_display_name(name):
    txt = " ".join((name or "").split()).strip()
    txt = re.sub(r"(?i)^\s*c\.?\s*n\.?\s+", "CN ", txt)
    txt = re.sub(r"(?i)^\s*club\s+natacio\s+", "CN ", txt)
    return txt or "Club"


def _club_key(name):
    txt = _club_display_name(name).lower()
    txt = unicodedata.normalize("NFD", txt)
    txt = "".join(ch for ch in txt if unicodedata.category(ch) != "Mn")
    txt = re.sub(r"[^a-z0-9]+", "", txt)
    return txt or "clubunknown"


def infer_club_from_team_name(team_name):
    base = (team_name or "").strip()
    base = re.sub(r"\s+\"?[A-D]\"?$", "", base)
    base = re.sub(r"\s+(MASC|FEM|MIXT|MIXTA|MASC\.?|FEM\.?)[\w\.\-\"]*$", "", base, flags=re.IGNORECASE)
    base = base.strip(" -") or (team_name or "Club")
    return {
        "club_id": f"inf-{_club_slug(base)}",
        "club_name": base,
    }


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
header{background:linear-gradient(135deg,var(--blue-dark),var(--blue));color:#fff;padding:.95rem .9rem .65rem;text-align:center}
.header-inner{display:flex;align-items:center;justify-content:center;gap:.75rem}
.club-logo{width:52px;height:52px;border-radius:50%;border:2px solid rgba(255,255,255,.6);flex-shrink:0}
header h1{font-size:1.18rem;font-weight:700}.subtitle{font-size:.76rem;opacity:.8}
main{max-width:780px;margin:0 auto;padding:.55rem}

/* Selection screen */
#selection-screen{display:block}
#detail-screen{display:none}
.selection-hero{background:linear-gradient(145deg,#ffffff,#eef8fd);border:1px solid #d7eaf3;border-radius:16px;padding:.8rem .85rem .75rem;margin:.1rem .15rem .7rem;box-shadow:0 6px 18px rgba(2,62,138,.08)}
.selection-eyebrow{font-size:.72rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--blue);margin-bottom:.25rem}
.sel-title{text-align:left;font-size:1.08rem;color:var(--blue-dark);margin:0 0 .15rem;font-weight:700}
.sel-subtitle{text-align:left;font-size:.8rem;color:var(--text-muted);margin:0 0 .65rem;max-width:52ch}
.selection-actions{display:flex;justify-content:flex-start;gap:.5rem;flex-wrap:wrap}
.selection-flow{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.45rem;margin:0 0 .7rem}
.flow-step{background:#f8fbfd;border:1px solid #d9e9f2;border-radius:10px;padding:.45rem .5rem}
.flow-step-k{display:block;font-size:.66rem;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.03em;margin-bottom:.2rem}
.flow-step-v{font-size:.78rem;color:var(--blue-dark);font-weight:600}
.flow-select{width:100%;font-size:.78rem;padding:.25rem .4rem;border:1px solid var(--blue-light);border-radius:6px;background:#fff;color:var(--blue-dark)}
.flow-select:focus{outline:none;border-color:var(--blue);box-shadow:0 0 0 2px rgba(0,119,182,.16)}
.searchable-select{position:relative}
.searchable-select::after{content:'▾';position:absolute;right:.45rem;top:50%;transform:translateY(-50%);pointer-events:none;color:var(--blue-dark);font-size:.8rem;line-height:1}
.searchable-select .flow-select{padding-right:1.5rem}
.select-options{position:absolute;top:100%;left:0;right:0;background:#fff;border:1px solid var(--blue-light);border-top:none;max-height:200px;overflow-y:auto;z-index:10;display:none}
.select-option{padding:.25rem .4rem;cursor:pointer;border-bottom:1px solid #e9ecef}
.select-option:hover{background:var(--blue-pale)}
.select-option:last-child{border-bottom:none}
.club-required-note{margin:.15rem .15rem .45rem;padding:.42rem .55rem;border-radius:8px;background:#fff8e8;border:1px solid #f2d48a;color:#73510a;font-size:.76rem}
.cat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:.6rem;padding:0 .15rem}
.cat-card{background:var(--card);border-radius:14px;padding:.8rem;cursor:pointer;border:1px solid #dbe7ee;transition:border-color .2s,box-shadow .2s,transform .15s;position:relative;overflow:hidden;box-shadow:0 4px 12px rgba(15,23,42,.04)}
.cat-card:hover{border-color:var(--blue-light);box-shadow:0 10px 24px rgba(0,119,182,.14);transform:translateY(-2px)}
.cat-card-top{display:flex;align-items:flex-start;justify-content:space-between;gap:.5rem;margin-bottom:.45rem}
.cat-card-name{font-size:.85rem;font-weight:800;color:var(--blue-dark);line-height:1.2}
.cat-card-age{display:inline-block;font-size:.66rem;color:var(--blue-dark);margin-bottom:.55rem;background:var(--blue-pale);padding:.14rem .4rem;border-radius:999px;font-style:normal;font-weight:600}
.cat-card-stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.35rem}
.cat-card-stat{background:#f8fbfd;border:1px solid #e2edf3;border-radius:9px;padding:.35rem .42rem}
.cat-card-stat-v{display:block;font-size:.84rem;font-weight:800;color:var(--text)}
.cat-card-stat-k{display:block;font-size:.69rem;color:var(--text-muted);margin-top:.05rem;text-transform:uppercase;letter-spacing:.03em}
.cat-card-arrow{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:999px;background:var(--blue-pale);font-size:.92rem;color:var(--blue-dark);flex-shrink:0}

/* Detail screen */
.back-bar{background:var(--card);border-bottom:1px solid #e0e0e0;padding:.4rem .65rem;display:flex;align-items:center;gap:.45rem}
.btn-back{background:none;border:1px solid var(--blue);color:var(--blue);padding:.24rem .55rem;border-radius:6px;font-size:.76rem;cursor:pointer;display:flex;align-items:center;gap:.3rem;transition:background .2s,color .2s}
.btn-back:hover{background:var(--blue);color:#fff}
.back-label{font-size:.82rem;color:var(--text-muted)}

.category-header{background:var(--card);border-radius:var(--radius);padding:.8rem;margin-bottom:.5rem;text-align:center}
.category-header h2{font-size:.95rem;color:var(--blue-dark);margin-bottom:.15rem}
.team-selector-wrap{margin:.4rem 0;display:flex;align-items:center;justify-content:center;gap:.4rem;flex-wrap:wrap}
.team-selector-label{font-size:.75rem;color:var(--text-muted)}
.team-selector{font-size:.78rem;padding:.25rem .45rem;border:1px solid var(--blue-light);border-radius:6px;color:var(--blue-dark);background:#fff;cursor:pointer;max-width:260px}
.team-selector:focus{outline:none;border-color:var(--blue);box-shadow:0 0 0 2px rgba(0,119,182,.2)}

.record-bar{display:inline-flex;gap:.35rem;font-size:.75rem}
.record-bar span{padding:.1rem .38rem;border-radius:4px;font-weight:600}
.record-bar .w{background:#d4edda;color:var(--green)}.record-bar .d{background:#fff3cd;color:#856404}
.record-bar .l{background:#f8d7da;color:var(--red)}.record-bar .gf{background:var(--blue-pale);color:var(--blue-dark)}
.record-bar .ga{background:#e9ecef;color:var(--text-muted)}
.section-block{background:var(--card);border-radius:var(--radius);padding:.65rem;margin-bottom:.5rem}
.section-block h3{font-size:.84rem;color:var(--blue-dark);border-bottom:2px solid var(--blue-pale);padding-bottom:.2rem;margin-bottom:.4rem;cursor:pointer;user-select:none;display:flex;align-items:center;justify-content:space-between}
.section-block h3 .toggle-arrow{font-size:.55rem;color:var(--blue-light);transition:transform .2s;display:inline-block}
.section-block.collapsed h3 .toggle-arrow{transform:rotate(180deg)}
.section-block.collapsed .section-content{display:none}
.empty{color:var(--text-muted);font-size:.85rem}
.next-match-card{background:linear-gradient(135deg,var(--blue),var(--blue-dark));color:#fff;border-radius:8px;padding:.8rem;text-align:center}
.next-date{font-size:.85rem;opacity:.85;margin-bottom:.35rem}
.next-teams{font-size:1.2rem;font-weight:700}
.next-teams .vs{margin:0 .4rem;opacity:.6;font-weight:400;font-size:.9rem}
.next-round{font-size:.75rem;opacity:.65;margin-top:.2rem}
.our-team{color:var(--blue)}.next-match-card .our-team{color:#ffd166}
.match-row{padding:.42rem .5rem;border-radius:8px;margin-bottom:.28rem;border-left:6px solid transparent;background:var(--bg);transition:box-shadow .15s,border-color .15s,background .15s}
.match-row:hover{box-shadow:0 1px 6px rgba(0,0,0,.06)}
.match-row.win{background:#eefaf1;border-left-color:#2e8b57}
.match-row.draw{background:#fff9e8;border-left-color:#c79a1b}
.match-row.loss{background:#fff1f1;border-left-color:#c94b4b}
.match-meta{display:flex;align-items:center;justify-content:space-between;gap:.4rem;font-size:.69rem;color:var(--text-muted);margin-bottom:.22rem}
.match-venue{margin-top:.18rem;font-size:.69rem;color:var(--text-muted);font-style:italic}
.match-teams{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:.25rem;font-size:.78rem}
.team-home{text-align:right;font-weight:500}.team-away{text-align:left;font-weight:500}
.match-score{display:flex;align-items:center;gap:.15rem;font-weight:700;font-size:.84rem;justify-content:center;padding:.14rem .34rem;border-radius:999px;border:1px solid #d7e1e8;background:#fff}
.match-row.win .match-score{background:#dff3e5;border-color:#86c79a;color:#1f6b43}
.match-row.draw .match-score{background:#fff1c7;border-color:#e2c15d;color:#8a6a09}
.match-row.loss .match-score{background:#fde0e0;border-color:#e7a2a2;color:#a73535}
.match-outcome{display:inline-block;padding:.1rem .38rem;border-radius:999px;font-size:.67rem;font-weight:700;letter-spacing:.02em}
.match-outcome.win{background:#dff3e5;color:#1f6b43}
.match-outcome.draw{background:#fff1c7;color:#8a6a09}
.match-outcome.loss{background:#fde0e0;color:#a73535}
.score-sep{color:var(--text-muted);font-size:.8rem}
.vs-small{color:var(--text-muted);font-size:.78rem}
.standings-block{margin-bottom:.6rem}
.standings-block h3{font-size:.82rem;color:var(--blue);margin-bottom:.3rem;border:none;padding:0}
.phase-header{font-size:.82rem;color:var(--blue);font-weight:600;margin:.7rem 0 .3rem;padding-bottom:.2rem;border-bottom:1px solid var(--blue)}
.phase-header:first-child{margin-top:0}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:.72rem}
th,td{padding:.28rem .24rem;text-align:center}
th{background:var(--blue-dark);color:#fff;font-weight:600;font-size:.7rem;position:sticky;top:0}
th,td{border:1px solid #d7e1e8}
td{background:#fff}
.team-name-cell{text-align:left!important;white-space:nowrap}
.pos{font-weight:700;color:var(--blue)}.pts{font-weight:700;color:var(--blue-dark)}
tr.highlight{background:var(--blue-pale)}tr.highlight td{font-weight:600}
.links-block{display:flex;flex-wrap:wrap;gap:.5rem;justify-content:center}
.btn-link{padding:.28rem .58rem;background:var(--blue);color:#fff;text-decoration:none;border-radius:6px;font-size:.75rem}
.btn-link:hover{background:var(--blue-dark)}
.roster-table{width:100%;border-collapse:collapse;font-size:.74rem}
.roster-table th{background:var(--blue-dark);color:#fff;font-weight:600;font-size:.69rem;padding:.28rem .32rem;text-align:center}
.roster-table td{padding:.24rem .3rem;border-bottom:1px solid #e9ecef}
.roster-name{font-weight:500}
.roster-age{text-align:center;font-weight:600;color:var(--blue-dark);font-size:.75rem}
.roster-role{color:var(--text-muted);font-size:.72rem;font-style:italic}
.roster-staff-title{font-size:.8rem;color:var(--blue);font-weight:600;margin:.6rem 0 .3rem;padding-top:.4rem;border-top:1px solid #e9ecef}
.insight-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:.45rem;margin-bottom:.5rem}
.insight-card{background:var(--bg);border-left:4px solid var(--blue);border-radius:8px;padding:.45rem .55rem}
.insight-card .k{font-size:.68rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:.03em}
.insight-card .v{font-size:1rem;font-weight:700;color:var(--blue-dark);line-height:1.15}
.insight-card .h{font-size:.7rem;color:var(--text-muted)}
.insight-summary{font-size:.8rem;color:var(--text);margin:.35rem 0 .55rem}
.player-badge{display:inline-block;padding:.12rem .35rem;border-radius:999px;font-size:.68rem;font-weight:600}
.player-badge.ok{background:#d4edda;color:var(--green)}
.player-badge.young{background:#fff3cd;color:#856404}
.player-badge.old{background:#f8d7da;color:var(--red)}
.player-badge.unknown{background:#e9ecef;color:var(--text-muted)}
footer{text-align:center;padding:.95rem .85rem;font-size:.69rem;color:var(--text-muted)}
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
/* Season selector */
.season-select-wrap{margin-top:.4rem;display:flex;align-items:center;justify-content:center;gap:.4rem}
.season-select{font-size:.78rem;padding:.25rem .5rem;border:1px solid rgba(255,255,255,.4);border-radius:6px;color:#fff;background:rgba(255,255,255,.15);cursor:pointer;-webkit-appearance:none;appearance:none;text-align:center;min-width:120px}
.season-select:focus{outline:none;border-color:rgba(255,255,255,.8)}
.season-select option{color:var(--text);background:var(--card)}
.top-actions{display:flex;justify-content:center;gap:.4rem;margin:0 auto .55rem;flex-wrap:wrap}
.btn-secondary{border:1px solid var(--blue);background:#fff;color:var(--blue-dark);padding:.28rem .55rem;border-radius:8px;font-size:.74rem;font-weight:600;cursor:pointer}
.btn-secondary:hover{background:var(--blue-pale)}
.player-screen-grid{display:block}
.player-list{background:var(--card);border-radius:var(--radius);padding:.45rem;max-height:68vh;overflow:auto}
.player-list.hidden{display:none}
.player-row{padding:.36rem .42rem;border:1px solid #e9ecef;border-radius:8px;margin-bottom:.3rem;cursor:pointer;background:#fff}
.player-row:hover{border-color:var(--blue-light);background:#f9fcff}
.player-row-name{font-size:.8rem;font-weight:700;color:var(--blue-dark)}
.player-row-meta{font-size:.7rem;color:var(--text-muted)}
.player-detail{background:var(--card);border-radius:var(--radius);padding:.45rem}
.player-detail-empty{color:var(--text-muted);font-size:.82rem}
.player-head{display:flex;align-items:flex-start;justify-content:space-between;gap:.45rem;margin-bottom:.38rem}
.player-head-main{min-width:0;flex:1}
.player-title{font-size:.94rem;font-weight:800;color:var(--blue-dark);line-height:1.15;margin:0}
.player-subtitle{font-size:.69rem;color:var(--text-muted);margin-top:.1rem;line-height:1.25}
.player-back-wrap{display:flex;justify-content:flex-start;flex-shrink:0}
.player-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(92px,1fr));gap:.28rem;margin-bottom:.35rem}
.player-kpi{background:var(--bg);border-radius:7px;padding:.22rem .3rem;border-left:3px solid var(--blue)}
.player-kpi .k{font-size:.59rem;color:var(--text-muted);text-transform:uppercase;line-height:1.1}
.player-kpi .v{font-size:.81rem;font-weight:700;color:var(--blue-dark);line-height:1.15;margin-top:.04rem}
.player-detail .section-block{padding:.5rem;margin-bottom:0}
.player-detail .section-block h3{font-size:.79rem;margin-bottom:.32rem}
.player-teams{display:flex;flex-wrap:wrap;gap:.3rem}
.player-chip{font-size:.68rem;background:var(--blue-pale);color:var(--blue-dark);padding:.12rem .4rem;border-radius:999px;cursor:pointer}
.compare-grid{display:grid;grid-template-columns:1fr;gap:.6rem}
.compare-panel{background:var(--card);border-radius:var(--radius);padding:.65rem}
.compare-head{display:flex;align-items:center;justify-content:space-between;gap:.5rem;flex-wrap:wrap;margin-bottom:.45rem}
.compare-head h4{margin:0;font-size:.85rem;color:var(--blue-dark)}
.compare-actions{display:flex;gap:.35rem;flex-wrap:wrap}
.compare-filter{width:100%;padding:.4rem .55rem;border:1px solid var(--blue-light);border-radius:8px;font-size:.78rem;margin-bottom:.45rem;box-sizing:border-box}
.compare-options{max-height:200px;overflow:auto;border:1px solid #e9ecef;border-radius:8px;padding:.4rem;background:#fff}
.compare-opt{display:flex;align-items:flex-start;gap:.35rem;padding:.2rem 0;border-bottom:1px solid #f1f3f5}
.compare-opt:last-child{border-bottom:none}
.compare-opt label{font-size:.76rem;line-height:1.25;cursor:pointer;flex:1}
.compare-results{margin-top:.5rem}
.player-chip:hover{background:var(--blue);color:#fff}
.player-search-wrap{margin:0 0 .45rem;position:relative}
.player-search-wrap .search-input{padding-left:.8rem}
.season-cats{display:none}.season-cats.active{display:block}
@media(max-width:480px){.selection-flow{grid-template-columns:1fr}.cat-grid{grid-template-columns:1fr}.match-row{grid-template-columns:52px 56px 1fr;padding:.4rem}.match-teams{font-size:.74rem}header h1{font-size:1.1rem}}
"""

# ---------------------------------------------------------------------------
# JS  – FIX: dsToDate() interpreta correctament UTC i date_local amb TZ
# ---------------------------------------------------------------------------
# CANVIS respecte la versió anterior:
#   1. Nova funció dsToDate(ds): si el string NO té indicador de timezone
#      (Z o +HH:MM), afegeix 'Z' per forçar interpretació UTC al navegador.
#      Si ja té timezone (camp dl / date_local generat pel Python), el
#      navegador l'interpreta directament i mostra l'hora local correcta.
#   2. fmtShort(ds, dl) i fmtLong(ds, dl): accepten el camp 'dl'
#      (date_local amb timezone) com a font preferent; fan servir 'ds'
#      (UTC cru) com a fallback via dsToDate().
#   3. Tots els llocs que criden fmtShort/fmtLong passen ara m.dl com a
#      segon argument.

JS = """
/* --- Helpers --- */
function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML;}
function titleCase(s){return s.split(' ').map(function(w){return w.charAt(0).toUpperCase()+w.slice(1).toLowerCase();}).join(' ');}
function normalizeSearchText(s){
    return (s||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'').trim();
}
function toggleSection(h3){h3.parentElement.classList.toggle('collapsed');}
function calcAge(bd,refDate){
  if(!bd||!refDate)return'';
  var p=bd.split('-');if(p.length<3)return'';
  var bY=parseInt(p[0]),bM=parseInt(p[1]),bD=parseInt(p[2]);
  var r=new Date(refDate+'T00:00:00');
  var age=r.getFullYear()-bY;
  if(r.getMonth()+1<bM||(r.getMonth()+1===bM&&r.getDate()<bD))age--;
  return age>=0?age:'';
}
function getAgeRef(entryId){
  var m=entryId.match(/^s(\\d+)-/);
  if(!m)return new Date().toISOString().slice(0,10);
  var sid=m[1];
  var s=(window.SEASONS||[]).find(function(x){return x.id===sid;});
  return s&&s.ageRef?s.ageRef:new Date().toISOString().slice(0,10);
}
function getSeasonAgeRef(seasonId){
    var s=(window.SEASONS||[]).find(function(x){return x.id===seasonId;});
    return s&&s.ageRef?s.ageRef:new Date().toISOString().slice(0,10);
}
function seasonIdFromEntryId(entryId){
    var m=(entryId||'').match(/^s(\\d+)-/);
    return m?m[1]:'';
}
function seasonLabelById(seasonId){
    var s=(window.SEASONS||[]).find(function(x){return x.id===seasonId;});
    return s?s.label:seasonId;
}
function getCategoryKey(name){
    var u=(name||'').toUpperCase();
    var keys=['BENJAMI','ALEVI','INFANTIL','CADET','JUVENIL','ABSOLUTA','MASTER'];
    for(var i=0;i<keys.length;i++)if(u.indexOf(keys[i])>=0)return keys[i];
    return '';
}
function getCategoryAgeRange(cat){
    var ranges={
        BENJAMI:[9,10],ALEVI:[11,12],INFANTIL:[13,14],CADET:[15,16],JUVENIL:[17,18],
        ABSOLUTA:[18,null],MASTER:[30,null]
    };
    return ranges[cat]||null;
}
function ageStatus(age,cat){
    if(age==null||age==='')return 'unknown';
    var r=getCategoryAgeRange(cat);
    if(!r)return 'unknown';
    if(age<r[0])return 'young';
    if(r[1]!=null&&age>r[1])return 'old';
    return 'ok';
}

/* --- Date helpers ---
 * dsToDate: converteix un string de data a objecte Date interpretant-lo
 *   sempre com a UTC quan no té indicador de timezone explícit.
 *   - "YYYY-MM-DD HH:MM:SS"  → afegeix 'Z'  → UTC → el navegador mostra
 *     l'hora local de l'usuari (que a Espanya és UTC+1/UTC+2).
 *   - "YYYY-MM-DDTHH:MM:SS+02:00" (dl / date_local) → ja té TZ, el
 *     navegador ho interpreta directament sense cap transformació.
 * fmtShort/fmtLong: prefereixen 'dl' (date_local amb TZ) si existeix;
 *   usen 'ds' (UTC cru) com a fallback.
 */
function dsToDate(ds){
  if(!ds)return null;
  /* Si el string ja conté 'Z' o un offset '+'/'-' no té timezone implícit */
  if(/[Z+]/.test(ds.slice(10)))return new Date(ds);
  /* String UTC cru sense TZ: afegim 'Z' per forçar UTC */
  return new Date(ds.replace(' ','T')+'Z');
}
function fmtShort(ds,dl){
  var dt=dsToDate(dl||ds);if(!dt||isNaN(dt))return'TBD';
  var d=('0'+dt.getDate()).slice(-2),mo=('0'+(dt.getMonth()+1)).slice(-2);
  var h=('0'+dt.getHours()).slice(-2),mi=('0'+dt.getMinutes()).slice(-2);
  return d+'/'+mo+' '+h+':'+mi;
}
function fmtLong(ds,dl){
  var dt=dsToDate(dl||ds);if(!dt||isNaN(dt))return'Per determinar';
  var days=['Dg','Dl','Dt','Dc','Dj','Dv','Ds'];
  var d=('0'+dt.getDate()).slice(-2),mo=('0'+(dt.getMonth()+1)).slice(-2);
  var h=('0'+dt.getHours()).slice(-2),mi=('0'+dt.getMinutes()).slice(-2);
  return days[dt.getDay()]+' '+d+'/'+mo+'/'+dt.getFullYear()+' '+h+':'+mi;
}

/* --- Season Switching --- */
function getSeasonObj(seasonId){
    return (window.SEASONS||[]).find(function(s){return s.id===seasonId;})||null;
}
function populateCategorySelect(){
    var sel = document.getElementById('category-select');
    var teamSel = document.getElementById('team-select');
    if(!sel) return;
    var clubId = window.CUR_CLUB || '';
    if(!clubId){
        sel.innerHTML = '<option value="">Selecciona categoria</option>';
        sel.disabled = true;
        if(teamSel){ teamSel.innerHTML = '<option value="">Selecciona equip</option>'; teamSel.disabled = true; }
        return;
    }
    var cards = document.querySelectorAll('.season-cats.active .cat-card[data-club="' + clubId + '"]');
    var catData = [];
    cards.forEach(function(card){
        var catId = card.dataset.catId || '';
        var teamCount = parseInt(card.dataset.teamCount || '0', 10);
        var lbl = card.dataset.catLabel || 'Categoria';
        if(!catId) return;
        catData.push({catId: catId, teamCount: teamCount, lbl: lbl});
    });
    catData.sort(function(a,b){return a.lbl.localeCompare(b.lbl,'ca');});
    var html = '<option value="">Selecciona categoria</option>';
    catData.forEach(function(item){
        html += '<option value="' + esc(item.catId) + '">' + esc(item.lbl) + ' (' + item.teamCount + ' equips)</option>';
    });
    sel.innerHTML = html;
    sel.value = '';
    sel.disabled = cards.length === 0;
    populateTeamSelect('');
}
function populateTeamSelect(catId){
    var sel = document.getElementById('team-select');
    if(!sel) return;
    if(!catId){
        sel.innerHTML = '<option value="">Selecciona equip</option>';
        sel.disabled = true;
        return;
    }
    var panel = document.getElementById('teams-' + catId);
    var cards = panel ? panel.querySelectorAll('.cat-card[data-detail]') : [];
    var teamData = [];
    cards.forEach(function(card){
        var detailId = card.dataset.detail || '';
        var teamId = card.dataset.teamId || '';
        var teamLabel = card.dataset.teamLabel || 'Equip';
        if(!detailId) return;
        teamData.push({detailId: detailId, teamId: teamId, teamLabel: teamLabel});
    });
    teamData.sort(function(a,b){return a.teamLabel.localeCompare(b.teamLabel,'ca');});
    var html = '<option value="">Selecciona equip</option>';
    teamData.forEach(function(item){
        html += '<option value="' + esc(item.detailId) + '" data-team-id="' + esc(item.teamId) + '">' + esc(item.teamLabel) + '</option>';
    });
    sel.innerHTML = html;
    sel.value = '';
    sel.disabled = cards.length === 0;
}
function populateClubSelect(seasonId){
    var sel = document.getElementById('club-select');
    if(!sel) return;
    var s = getSeasonObj(seasonId);
    var clubs = (s && s.clubs) ? s.clubs : [];
    var html = '<option value="">Selecciona club</option>';
    clubs.forEach(function(c){
        html += '<option value="' + esc(c.id) + '">' + esc(c.name) + '</option>';
    });
    sel.innerHTML = html;
    sel.value = '';
    sel.disabled = false;
    window.CUR_CLUB = '';
    populateCategorySelect();
}
function applyClubFilter(){
    var seasonId=window.CUR_SEASON||'';
    var clubId=window.CUR_CLUB||'';
    var note=document.getElementById('club-required-note');
    var visibleCats=0;
    document.querySelectorAll('.season-cats.active .cat-card').forEach(function(card){
        var ok=clubId && card.dataset.club===clubId;
        card.style.display=ok?'':'none';
        if(ok)visibleCats++;
    });
    if(note){
        if(!clubId)note.style.display='block';
        else if(visibleCats===0){
            note.style.display='block';
            note.textContent='No hi ha categories disponibles per aquest club en aquesta temporada.';
        }else{
            note.style.display='none';
            note.textContent='Selecciona primer un club per desbloquejar les categories.';
        }
    }
    var sub=document.querySelector('.subtitle');
    if(sub){
        var si=getSeasonObj(seasonId);
        var status=(si&&!si.current)?' (temporada tancada)':'';
        sub.textContent=(clubId?visibleCats+' categories':'Tria temporada i club')+status+(si&&si.ra?' · Actualitzat: '+si.ra:'');
    }
    populateCategorySelect();
}
function switchClub(clubId){
    window.CUR_CLUB=clubId||'';
    applyClubFilter();
    if(!window.CUR_CLUB)showCategories();
}
function switchCategory(catId){
    populateTeamSelect(catId || '');
    if(!catId) return;
    var sel = document.getElementById('team-select');
    /* If only one real option, auto-select it */
    if(sel && sel.options.length === 2){
        sel.selectedIndex = 1;
        switchTeamFromSelect(sel);
    }
}
function switchTeamFromSelect(sel){
    var detailId = sel.value;
    if(!detailId) return;
    var opt = sel.options[sel.selectedIndex];
    var teamId = opt ? (opt.getAttribute('data-team-id') || '') : '';
    switchTeam(detailId, teamId);
}
function switchTeam(detailId, teamId){
    if(!detailId || !window.CUR_CLUB) return;
    if(!teamId){
        var sel = document.getElementById('team-select'); // old, but if exists
        if(sel && sel.selectedIndex >= 0){
            teamId = sel.options[sel.selectedIndex].getAttribute('data-team-id') || '';
        }
    }
    showDetail(detailId, teamId || undefined);
}
function switchSeason(seasonId){
  window.CUR_SEASON=seasonId;
  _searchIdx=null;
    _playerIdx=null;
    document.querySelectorAll('.season-cats').forEach(function(el){
    if(el.dataset.season===seasonId)el.classList.add('active');
    else el.classList.remove('active');
  });
  document.querySelectorAll('.detail-category').forEach(function(c){c.style.display='none';});
  var sel=document.getElementById('season-select');
  if(sel)sel.value=seasonId;
    populateClubSelect(seasonId);
    applyClubFilter();
  showCategories();
  clearSearch();
}



/* --- Player Search --- */
var _searchIdx=null;
var _playerIdx=null;
function buildSearchIndex(){
  if(_searchIdx)return _searchIdx;
  var prefix='s'+(window.CUR_SEASON||'')+'-';
  var teamTournMap={};
  Object.keys(window.WP).forEach(function(eid){
    if(eid.indexOf(prefix)!==0)return;
    var d=window.WP[eid];
    Object.keys(d.teams).forEach(function(tid){
      if(!teamTournMap[tid])teamTournMap[tid]=[];
      teamTournMap[tid].push({eid:eid,tname:d.tname,label:d.label||d.tname,teamName:d.teams[tid],tid:tid});
    });
  });
  var persons={};
  var rost=window.ROST||{};
  Object.keys(rost).forEach(function(tid){
    if(!teamTournMap[tid])return;
    rost[tid].forEach(function(p){
      var k=p.fn+'|'+p.ln+'|'+(p.bd||'');
      if(!persons[k])persons[k]={fn:p.fn,ln:p.ln,bd:p.bd,ro:p.ro,teams:[]};
      if(p.ro==='player')persons[k].ro='player';
      var tours=teamTournMap[tid]||[];
      tours.forEach(function(t){
        var already=persons[k].teams.some(function(x){return x.eid===t.eid&&x.teamName===t.teamName;});
        if(!already)persons[k].teams.push({eid:t.eid,tname:t.tname,label:t.label,teamName:t.teamName,tid:t.tid});
      });
    });
  });
  _searchIdx=Object.values(persons);
  _searchIdx.forEach(function(p){
        p._s=normalizeSearchText(p.fn+' '+p.ln);
  });
  return _searchIdx;
}
function doSearch(q){
  var res=document.getElementById('search-results');
  var clear=document.getElementById('search-clear');
  if(!q||q.length<2){res.innerHTML='';res.style.display='none';clear.style.display='none';return;}
  clear.style.display='block';
  var idx=buildSearchIndex();
    var ql=normalizeSearchText(q);
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
      tags+='<span class="search-result-tag" onclick="clearSearch();showDetail(\\''+t.eid+'\\',\\''+t.tid+'\\')"><strong>'+esc(lbl)+'</strong> ('+esc(t.teamName)+')</span>';
    });
    html+='<div class="search-result-item"><div><span class="search-result-name">'+name+'</span>'+byH+'<span class="search-result-role">'+role+'</span></div><div class="search-result-teams">'+tags+'</div></div>';
  });
  if(hits.length>=50)html+='<div class="search-empty">Mostrant 50 de mes resultats...</div>';
  res.innerHTML=html;res.style.display='block';
}
function clearSearch(){
  var inp=document.getElementById('search-input');
  if(inp){inp.value='';doSearch('');}
}
function buildPlayerIndex(){
    if(_playerIdx)return _playerIdx;
    var ageRef=getSeasonAgeRef(window.CUR_SEASON||'');
    var teamTournMap={};
    Object.keys(window.WP||{}).forEach(function(eid){
        var d=window.WP[eid]||{};
        Object.keys(d.teams||{}).forEach(function(tid){
            if(!teamTournMap[tid])teamTournMap[tid]=[];
            teamTournMap[tid].push({
                eid:eid,
                tid:tid,
                tname:d.tname||'',
                label:d.label||d.tname||'',
                teamName:d.teams[tid]||''
            });
        });
    });

    var persons={};
    Object.keys(window.ROST||{}).forEach(function(tid){
        var tours=teamTournMap[tid]||[];
        if(tours.length===0)return;
        (window.ROST[tid]||[]).forEach(function(p){
            var k=p.fn+'|'+p.ln+'|'+(p.bd||'');
            if(!persons[k])persons[k]={fn:p.fn,ln:p.ln,bd:p.bd||'',teams:[],roleRows:[]};
            var role=(p.ro||'').toLowerCase()==='player'?'player':'staff';
            tours.forEach(function(t){
                var item=persons[k].teams.find(function(x){return x.eid===t.eid&&x.tid===t.tid;});
                if(!item){
                    item={
                        eid:t.eid,
                        tid:t.tid,
                        tname:t.tname,
                        label:t.label,
                        teamName:t.teamName,
                        player:false,
                        staff:false
                    };
                    persons[k].teams.push(item);
                }
                if(role==='player')item.player=true;
                else item.staff=true;
                var sid=seasonIdFromEntryId(t.eid)||'';
                persons[k].roleRows.push({sid:sid,role:role});
            });
        });
    });

    _playerIdx=Object.keys(persons).map(function(k){
        var p=persons[k];
        var teams=[];var teamSeen={};
        var labels=[];var labelSeen={};
        var seasons=[];var seasonSeen={};
        p.teams.forEach(function(t){
            var sid=seasonIdFromEntryId(t.eid);
            var tk=sid+'|'+t.tid+'|'+t.teamName;
            if(!teamSeen[tk]){teamSeen[tk]=1;teams.push(t);} 
            var lbl=t.label||t.tname;
            if(!labelSeen[lbl]){labelSeen[lbl]=1;labels.push(lbl);} 
            if(sid&&!seasonSeen[sid]){seasonSeen[sid]=1;seasons.push(sid);} 
        });
        var hasPlayer=false;var hasStaff=false;
        var lastPlayerSeason='';var lastStaffSeason='';
        p.roleRows.forEach(function(rr){
            if(rr.role==='player'){
                hasPlayer=true;
                if(rr.sid && (!lastPlayerSeason || rr.sid>lastPlayerSeason))lastPlayerSeason=rr.sid;
            } else {
                hasStaff=true;
                if(rr.sid && (!lastStaffSeason || rr.sid>lastStaffSeason))lastStaffSeason=rr.sid;
            }
        });
        var rolePath='Jugador';
        if(hasPlayer&&hasStaff){
            rolePath=(lastStaffSeason && lastPlayerSeason && lastStaffSeason>=lastPlayerSeason)?'Jugador -> Staff':'Jugador + Staff';
        } else if(!hasPlayer&&hasStaff){
            rolePath='Staff';
        }
        seasons.sort();
        return {
            k:k,
            fn:p.fn,ln:p.ln,bd:p.bd||'',
            name:titleCase(p.fn)+' '+titleCase(p.ln),
            age:calcAge(p.bd,ageRef),
            teams:teams,
            labels:labels,
            seasons:seasons,
            hasPlayer:hasPlayer,
            hasStaff:hasStaff,
            movedToStaff:hasPlayer&&hasStaff&&lastStaffSeason&&lastPlayerSeason&&lastStaffSeason>=lastPlayerSeason,
            rolePath:rolePath,
            _s:normalizeSearchText(p.fn+' '+p.ln)
        };
    }).sort(function(a,b){return a.name.localeCompare(b.name);});

    return _playerIdx;
}
function showPlayers(){
    showScreen('player-screen');
    var q=document.getElementById('player-search-input');
    renderPlayerExplorer(q?q.value:'');
    history.replaceState(null,'','#players');
}
function backToPlayerResults(){
    var list=document.getElementById('player-list');
    var detail=document.getElementById('player-detail');
    if(list)list.classList.remove('hidden');
    if(detail)detail.innerHTML='<div class="player-detail-empty">Selecciona un jugador per veure la fitxa.</div>';
}
function playerClearSearch(){
    var inp=document.getElementById('player-search-input');
    if(inp)inp.value='';
    renderPlayerExplorer('');
}
function renderPlayerExplorer(q){
    var list=document.getElementById('player-list');
    var detail=document.getElementById('player-detail');
    if(!list)return;
    list.classList.remove('hidden');
    var all=buildPlayerIndex();
    var query=normalizeSearchText(q);
    var words=query?query.split(/\s+/):[];
    var hits=all.filter(function(p){return words.every(function(w){return p._s.indexOf(w)>=0;});});
    if(hits.length>300)hits=hits.slice(0,300);
    window._playerRenderList=hits;
    if(hits.length===0){
        list.innerHTML='<div class="player-detail-empty">Cap jugador per aquest filtre.</div>';
        if(detail)detail.innerHTML='<div class="player-detail-empty">Selecciona un jugador per veure la fitxa.</div>';
        return;
    }
    var html='';
    hits.forEach(function(p,i){
        var y=p.bd?p.bd.slice(0,4):'-';
        html+='<div class="player-row" onclick="openPlayerByIdx('+i+')">'+
            '<div class="player-row-name">'+esc(p.name)+'</div>'+
            '<div class="player-row-meta">Any '+esc(y)+' · '+p.teams.length+' equips · '+p.seasons.length+' temporades · '+esc(p.rolePath)+'</div>'+
            '</div>';
    });
    list.innerHTML=html;
    if(detail)detail.innerHTML='<div class="player-detail-empty">Selecciona un jugador per veure la fitxa.</div>';
}
function openPlayerByIdx(i){
    var p=window._playerRenderList&&window._playerRenderList[i];
    var list=document.getElementById('player-list');
    var detail=document.getElementById('player-detail');
    if(!p||!detail)return;
    if(list)list.classList.add('hidden');
    var firstS=p.seasons.length?seasonLabelById(p.seasons[0]):'-';
    var lastS=p.seasons.length?seasonLabelById(p.seasons[p.seasons.length-1]):'-';
    function roleLabel(player,staff){
        if(player&&staff)return 'Jugador + Staff';
        if(player)return 'Jugador';
        return 'Staff';
    }
    function mergedRowsHtml(rows){
        if(!rows||rows.length===0)return '';
        var cols=rows[0].length;
        var spans=[];
        for(var r=0;r<rows.length;r++){
            spans[r]=[];
            for(var c=0;c<cols;c++)spans[r][c]={show:true,span:1};
        }
        for(var c=0;c<cols;c++){
            var i0=0;
            while(i0<rows.length){
                var j=i0+1;
                while(j<rows.length){
                    var samePrefix=true;
                    for(var k=0;k<c;k++){
                        if(rows[j][k]!==rows[i0][k]){samePrefix=false;break;}
                    }
                    if(!samePrefix||rows[j][c]!==rows[i0][c])break;
                    j++;
                }
                var sp=j-i0;
                if(sp>1){
                    spans[i0][c].span=sp;
                    for(var r2=i0+1;r2<j;r2++)spans[r2][c].show=false;
                }
                i0=j;
            }
        }
        var out='';
        for(var r=0;r<rows.length;r++){
            out+='<tr>';
            for(var c=0;c<cols;c++){
                if(!spans[r][c].show)continue;
                var rs=spans[r][c].span>1?' rowspan="'+spans[r][c].span+'"':'';
                out+='<td'+rs+'>'+rows[r][c]+'</td>';
            }
            out+='</tr>';
        }
        return out;
    }
    function normLabel(s){
        return (s||'').toUpperCase().replace(/[\.]/g,'').replace(/\s+/g,' ').trim();
    }
    var bySeason={};
    p.teams.forEach(function(t){
        var sid=seasonIdFromEntryId(t.eid)||'';
        if(!sid)return;
        if(!bySeason[sid])bySeason[sid]={label:seasonLabelById(sid),cats:{},teams:{},pairs:{},player:false,staff:false};
        var cat=t.label||t.tname||'';
        var team=t.teamName||'';
        var nCat=normLabel(cat);
        var nTeam=normLabel(team);
        if(cat&&nCat&&!bySeason[sid].cats[nCat])bySeason[sid].cats[nCat]=cat;
        if(team&&nTeam&&!bySeason[sid].teams[nTeam])bySeason[sid].teams[nTeam]=team;
        if(t.player)bySeason[sid].player=true;
        if(t.staff)bySeason[sid].staff=true;
        if(nCat&&nTeam){
            var pairKey=nCat+'|'+nTeam;
            if(!bySeason[sid].pairs[pairKey])bySeason[sid].pairs[pairKey]={cat:cat,team:team,player:false,staff:false};
            if(t.player)bySeason[sid].pairs[pairKey].player=true;
            if(t.staff)bySeason[sid].pairs[pairKey].staff=true;
        }
    });
    var flatRowsData=[];
    Object.keys(bySeason).sort().forEach(function(sid){
        var item=bySeason[sid];
        var pairVals=Object.keys(item.pairs).map(function(k){return item.pairs[k];});
        if(pairVals.length===0){
            flatRowsData.push([esc(item.label),'-','-','-']);
        } else {
            pairVals.forEach(function(pv){
                flatRowsData.push([esc(item.label),esc(pv.cat),esc(pv.team),roleLabel(pv.player,pv.staff)]);
            });
        }
    });
    var flatRows=mergedRowsHtml(flatRowsData);
    if(!flatRows){
        flatRows='<tr><td colspan="4" class="player-detail-empty">Sense dades de temporada.</td></tr>';
    }
    detail.innerHTML=
        '<div class="player-head">'+
        '<div class="player-head-main">'+
        '<h3 class="player-title">'+esc(p.name)+'</h3>'+
        '<div class="player-subtitle">'+esc(firstS+' → '+lastS)+' · '+p.seasons.length+' temporades · '+p.teams.length+' equips</div>'+
        '</div>'+
        '<div class="player-back-wrap"><button class="btn-secondary" onclick="backToPlayerResults()">&larr; Tornar</button></div>'+
        '</div>'+
        '<div class="player-kpis">'+
        '<div class="player-kpi"><div class="k">Edat actual</div><div class="v">'+(p.age===''?'-':p.age)+'</div></div>'+
        '<div class="player-kpi"><div class="k">Rol detectat</div><div class="v">'+esc(p.rolePath)+'</div></div>'+
        '<div class="player-kpi"><div class="k">Pas a staff</div><div class="v">'+(p.movedToStaff?'Si':'No')+'</div></div>'+
        '<div class="player-kpi"><div class="k">Temporades</div><div class="v">'+p.seasons.length+'</div></div>'+
        '<div class="player-kpi"><div class="k">Equips</div><div class="v">'+p.teams.length+'</div></div>'+
        '<div class="player-kpi"><div class="k">Trajectoria</div><div class="v">'+esc(firstS+' → '+lastS)+'</div></div>'+
        '</div>'+
        '<div class="section-block"><h3>Relacio categoria-equip (files)</h3><div class="section-content"><div class="table-wrap"><table class="roster-table"><thead><tr><th>Temporada</th><th>Categoria</th><th>Equip</th><th>Rol</th></tr></thead><tbody>'+flatRows+'</tbody></table></div></div></div>';
}

/* --- Navigation --- */
function showScreen(name){
        ['selection-screen','player-screen','detail-screen'].forEach(function(s){
    document.getElementById(s).style.display=s===name?'block':'none';
  });
  window.scrollTo(0,0);
}
function showCategories(){showScreen('selection-screen');history.replaceState(null,'','#');}
function showTeams(catId){
    if(!window.CUR_CLUB){showCategories();return;}
    var catSel = document.getElementById('category-select');
    if(catSel) catSel.value = catId;
    populateTeamSelect(catId);
    var teamSel = document.getElementById('team-select');
    if(teamSel) teamSel.focus();
    showCategories();
    history.replaceState(null,'','#cat-'+catId);
}
function showDetail(id, teamId){
    if(!window.CUR_CLUB){showCategories();return;}
  showScreen('detail-screen');
  document.querySelectorAll('.detail-category').forEach(function(c){c.style.display='none';});
  var el=document.getElementById(id);
    if(el&&el.dataset.club===window.CUR_CLUB){
    el.style.display='block';
    var catId=el.dataset.catId,numTeams=parseInt(el.dataset.numTeams)||1;
    var catSel=document.getElementById('category-select');
    if(catSel) catSel.value = catId;
    populateTeamSelect(catId);
    var teamSel=document.getElementById('team-select');
    if(teamSel) teamSel.value = id;
    var catLabel=el.dataset.catLabel||'';
    var btn=document.getElementById('detail-back-btn');
    var lbl=document.getElementById('detail-back-label');
    if(numTeams>1){btn.onclick=function(){showTeams(catId);};lbl.textContent=catLabel;}
    else{btn.onclick=function(){showCategories();};lbl.textContent='Totes les categories';}
    /* Render default team */
    var data=window.WP[id];
    if(data){
      var sel=el.querySelector('.team-selector');
      if(sel)sel.value=teamId||data.dt;
      renderForTeam(id,teamId||data.dt);
    }
  }
  history.replaceState(null,'','#'+id);
}
function showDetailOrTeams(catId,teamCount){
    if(!window.CUR_CLUB){showCategories();return;}
    showTeams(catId);
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
  var past=teamMatches.filter(function(m){return m.f;}).sort(function(a,b){return(b.ts||0)-(a.ts||0);});
  var future=teamMatches.filter(function(m){return !m.f&&m.d;}).sort(function(a,b){return(a.ts||0)-(b.ts||0);});

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
      '<div class="next-date">'+fmtLong(nm.d,nm.dl)+'</div>'+
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
                '<td class="pts">'+s.pts+'</td><td>'+s.pj+'</td><td>'+s.pg+'</td><td>'+s.pe+'</td><td>'+s.pp+'</td>'+
                '<td>'+s.gf+'</td><td>'+s.gc+'</td><td>'+(s.dg>=0?'+':'')+s.dg+'</td></tr>';
    });
    stH+='<div class="standings-block"><h3>'+esc(g.n)+'</h3>'+
      '<div class="table-wrap"><table><thead><tr>'+
            '<th>#</th><th>Equip</th><th>Pts</th><th>PJ</th><th>PG</th><th>PE</th>'+
            '<th>PP</th><th>GF</th><th>GC</th><th>DG</th>'+
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
                var outcomeLabel=cls==='win'?'Victoria':cls==='loss'?'Derrota':'Empat';
        var hN=esc(data.teams[m.h]||'?'),aN=esc(data.teams[m.a]||'Descansa');
        var venueR=m.v?'<div class="match-venue">'+esc(m.v)+'</div>':'';
        rH+='<div class="match-row '+cls+'">'+
                    '<div class="match-meta"><span>'+fmtShort(m.d,m.dl)+'</span><span>'+esc(m.rn)+'</span><span class="match-outcome '+cls+'">'+outcomeLabel+'</span></div>'+
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
        '<div class="match-meta"><span>'+fmtShort(m.d,m.dl)+'</span><span>'+esc(m.rn)+'</span></div>'+
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
    var ageRef=getAgeRef(entryId);
    /* Deduplicate by fn+ln+bd */
    var seen={};var uRoster=[];
    roster.forEach(function(p){var k=p.fn+'|'+p.ln+'|'+(p.bd||'');if(!seen[k]){seen[k]=1;uRoster.push(p);}});
    var players=uRoster.filter(function(p){return p.ro==='player';});
    /* Sort players oldest first (birthdate ascending = oldest first) */
    players.sort(function(a,b){return(a.bd||'9999').localeCompare(b.bd||'9999');});
    var staff=uRoster.filter(function(p){return p.ro!=='player';});
    var rows='';
    players.forEach(function(p){
      var bd='',age='';
      if(p.bd){var pts=p.bd.split('-');if(pts.length>=3)bd=pts[2]+'/'+pts[1]+'/'+pts[0];age=calcAge(p.bd,ageRef);}
      var name=esc(titleCase(p.fn)+' '+titleCase(p.ln));
      rows+='<tr><td class="roster-name">'+name+'</td><td>'+bd+'</td><td>'+age+'</td></tr>';
    });
    var srows='';
    staff.forEach(function(p){
      var bd='',age='';
      if(p.bd){var pts=p.bd.split('-');if(pts.length>=3)bd=pts[2]+'/'+pts[1]+'/'+pts[0];age=calcAge(p.bd,ageRef);}
      var name=esc(titleCase(p.fn)+' '+titleCase(p.ln));
      srows+='<tr><td class="roster-name">'+name+'</td><td>'+bd+'</td><td>'+age+'</td></tr>';
    });
    rosH='<div class="section-block collapsed"><h3 onclick="toggleSection(this)">Plantilla ('+players.length+' jugadors)<span class="toggle-arrow">\u25B2</span></h3>'+
      '<div class="section-content"><div class="table-wrap"><table class="roster-table"><thead><tr><th>Nom</th><th>Naix.</th><th>Edat</th></tr></thead>'+
      '<tbody>'+rows+'</tbody></table></div>';
    if(srows)rosH+='<div class="roster-staff-title">Cos tecnic ('+staff.length+')</div>'+
      '<div class="table-wrap"><table class="roster-table"><thead><tr><th>Nom</th><th>Naix.</th><th>Edat</th></tr></thead>'+
      '<tbody>'+srows+'</tbody></table></div>';
    rosH+='</div></div>';
  }
  document.getElementById('roster-'+entryId).innerHTML=rosH;

  /* Links */
  document.getElementById('links-'+entryId).innerHTML=
    '<a href="'+clupik+'/es/tournament/'+data.tid+'/summary" target="_blank" rel="noopener" class="btn-link">Veure competicio completa</a>'+
    '<a href="'+clupik+'/es/team/'+teamId+'" target="_blank" rel="noopener" class="btn-link">'+esc(teamName)+'</a>';
}

/* Searchable select functions removed – replaced by native <select> elements */

/* --- Init --- */
window.addEventListener('DOMContentLoaded',function(){
  var defaultSeason=window.CUR_SEASON||'';
  var h=location.hash.slice(1);
  if(h){
    var m=h.match(/^(?:cat-)?s(\\d+)-/);
    if(m){
      var hs=m[1];
      if((window.SEASONS||[]).some(function(s){return s.id===hs;})){defaultSeason=hs;}
    }
  }
  if(defaultSeason)switchSeason(defaultSeason);
  if(!h)return;
    if(h==='players'){showPlayers();return;}
  if(h.startsWith('cat-')){showTeams(h.slice(4));}
  else{var el=document.getElementById(h);if(el&&el.classList.contains('detail-category'))showDetail(h);}
});
"""


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def generate_html(all_season_data, config):
    """Generate the complete HTML with multi-season support.

    all_season_data: OrderedDict of season_id -> {label, status, categories_data, category_age}
    """
    clupik = config.get("clupik_base_url", CLUPIK_BASE)
    build_time = datetime.utcnow().strftime("%d/%m/%Y %H:%M UTC")

    # Determine default season (first current, or first overall)
    default_season = None
    for sid, sdata in all_season_data.items():
        if sdata["status"] == "current":
            default_season = sid
            break
    if not default_season:
        default_season = next(iter(all_season_data))

    # Build season selector options (sorted alphabetically by label)
    season_options_html = ""
    sorted_seasons = sorted(all_season_data.items(), key=lambda x: x[1]["label"])
    for sid, sdata in sorted_seasons:
        tag = " (En curs)" if sdata["status"] == "current" else ""
        sel = " selected" if sid == default_season else ""
        season_options_html += f'<option value="{sid}"{sel}>{escape(sdata["label"])}{tag}</option>'

    # Process each season
    all_wp = {}           # flat WP data across all seasons (season-prefixed keys)
    all_rost = {}         # flat rosters (keyed by team_id, no prefix needed)
    cat_blocks = []       # per-season category card HTML blocks
    team_blocks = []      # per-season team panel HTML blocks
    all_detail_sects = [] # all detail sections (across seasons)
    seasons_json = []     # for window.SEASONS
    total_cats_default = 0

    for sid, sdata in all_season_data.items():
        categories_data = sdata["categories_data"]
        cat_age = sdata.get("category_age", CATEGORY_AGE)
        is_default = (sid == default_season)

        # --- Explode categories into per-team entries (multi-club) ---
        entries = []
        for cat in categories_data:
            teams = list(cat.get("teams") or [])
            # Always supplement with any teams from standings not already in the list.
            # This handles old-format caches where only the tracked club's teams were
            # stored (old 'our_teams' field), so all clubs appear in every season.
            existing_ids = {str(t["id"]) for t in teams}
            for g in cat.get("groups", []):
                for row in g.get("standings", []):
                    tid = str(row.get("id", ""))
                    if not tid or tid in existing_ids:
                        continue
                    tname = cat.get("team_names", {}).get(tid, f"Equip {tid}")
                    inferred = infer_club_from_team_name(tname)
                    teams.append({
                        "id": tid,
                        "name": tname,
                        "avatar": "",
                        "club_id": inferred["club_id"],
                        "club_name": inferred["club_name"],
                    })
                    existing_ids.add(tid)

            for team in teams:
                team_id = str(team["id"])
                team_ids = {team_id}
                team_matches = [m for m in cat["matches"]
                               if m["home_team"] in team_ids or m["away_team"] in team_ids]
                team_groups = [g for g in cat["groups"] if team_id in g.get("team_ids", set())]
                inferred = infer_club_from_team_name(team.get("name", ""))
                raw_club_name = str(team.get("club_name") or inferred["club_name"])
                club_name = _club_display_name(raw_club_name)
                club_id = f"club-{_club_key(club_name)}"
                entries.append({
                    "tournament_id": cat["tournament_id"],
                    "tournament_name": cat["tournament_name"],
                    "team": {**team, "id": team_id},
                    "club_id": club_id,
                    "club_name": club_name,
                    "team_ids": team_ids,
                    "matches": team_matches,
                    "all_groups": cat["groups"],
                    "all_matches": cat["matches"],
                    "team_groups": team_groups,
                    "team_names": cat["team_names"],
                    "rosters": cat.get("rosters", {}),
                })

        # --- Group entries by tournament for 2-level nav ---
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
                             key=lambda tid: category_age_info(tournaments_map[tid]["tournament_name"], cat_age)[0])
        tournaments_map = OrderedDict((tid, tournaments_map[tid]) for tid in sorted_tids)

        if is_default:
            total_cats_default = len(tournaments_map)

        clubs_map = OrderedDict()
        for entry in entries:
            cid = entry["club_id"]
            if cid not in clubs_map:
                clubs_map[cid] = {"id": cid, "name": entry["club_name"], "categories": set()}
            clubs_map[cid]["categories"].add(entry["tournament_id"])

        seasons_json.append({
            "id": sid,
            "label": sdata["label"],
            "current": sdata["status"] == "current",
            "ageRef": sdata.get("age_ref_date", datetime.now().strftime("%Y-%m-%d")),
            "ra": sdata.get("refreshed_at", ""),
            "clubs": sorted([
                {
                    "id": c["id"],
                    "name": c["name"],
                    "categories": len(c["categories"]),
                }
                for c in clubs_map.values()
            ], key=lambda x: x["name"]),
        })

        # --- Screen 1: Category cards for this season ---
        cat_cards_html = ""
        season_finished_match_ids = set()
        for tid, tinfo in tournaments_map.items():
            label = short_category(tinfo["tournament_name"])
            _, age_label = category_age_info(tinfo["tournament_name"], cat_age)

            by_club = OrderedDict()
            for e in tinfo["entries"]:
                by_club.setdefault(e["club_id"], []).append(e)

            for club_id, club_entries in by_club.items():
                cat_id = f"s{sid}-{slug(tinfo['tournament_name'])}-{slug(club_id)}"
                num_teams = len(club_entries)

                total_past = 0
                seen_mid = set()
                for e in club_entries:
                    for m in e["matches"]:
                        if m["finished"]:
                            mid = m.get("id")
                            if mid and mid not in seen_mid:
                                seen_mid.add(mid)
                                total_past += 1
                                season_finished_match_ids.add(mid)

                age_html = f'<div class="cat-card-age">{escape(age_label)}</div>' if age_label else ''
                cat_cards_html += (
                    f'<div class="cat-card" data-club="{escape(club_id)}" data-cat-id="{cat_id}" data-cat-label="{escape(label)}" data-team-count="{num_teams}" onclick="showDetailOrTeams(\'{cat_id}\',{num_teams})">'
                    f'<div class="cat-card-top">'
                    f'<div class="cat-card-name">{escape(label)}</div>'
                    f'<span class="cat-card-arrow">&#8250;</span>'
                    f'</div>'
                    f'{age_html}'
                    f'<div class="cat-card-stats">'
                    f'<div class="cat-card-stat"><span class="cat-card-stat-v">{num_teams}</span><span class="cat-card-stat-k">equip{"s" if num_teams > 1 else ""}</span></div>'
                    f'<div class="cat-card-stat"><span class="cat-card-stat-v">{total_past}</span><span class="cat-card-stat-k">partits jugats</span></div>'
                    f'</div>'
                    f'</div>'
                )

        active_cls = " active" if is_default else ""
        cat_blocks.append(
            f'<div class="season-cats{active_cls}" data-season="{sid}">'
            f'<div class="cat-grid">{cat_cards_html}</div>'
            f'</div>'
        )

        # --- Screen 2: Team panels for this season ---
        team_panels_html = ""
        for tid, tinfo in tournaments_map.items():
            label = short_category(tinfo["tournament_name"])

            by_club = OrderedDict()
            for e in tinfo["entries"]:
                by_club.setdefault(e["club_id"], []).append(e)

            for club_id, club_entries in by_club.items():
                cat_id = f"s{sid}-{slug(tinfo['tournament_name'])}-{slug(club_id)}"
                team_cards = ""
                for entry in club_entries:
                    team = entry["team"]
                    team_ids = entry["team_ids"]
                    entry_id = f"s{sid}-{slug(entry['tournament_name'] + '-' + team['name'])}"
                    team_name = escape(team["name"])

                    past = [m for m in entry["matches"] if m["finished"]]
                    future = [m for m in entry["matches"] if not m["finished"] and m["date"]]
                    past.sort(key=lambda m: m.get("date_ts") or 0, reverse=True)
                    future.sort(key=lambda m: m.get("date_ts") or 9999999999)

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

                    team_cards += (
                        f'<div class="cat-card" data-detail="{entry_id}" data-team-id="{escape(team["id"])}" data-team-label="{team_name}" onclick="showDetail(\'{entry_id}\',\'{team["id"]}\')">'
                        f'<div class="cat-card-name">{team_name}</div>'
                        f'<div class="cat-card-record">'
                        f'<span class="w">{wins}V</span><span class="d">{draws}E</span>'
                        f'<span class="l">{losses}D</span></div>'
                        f'{card_next}'
                        f'<span class="cat-card-arrow">&#8250;</span>'
                        f'</div>'
                    )

                team_panels_html += (
                    f'<div class="team-panel" data-club="{escape(club_id)}" id="teams-{cat_id}" style="display:none">'
                    f'<div class="sel-title">{escape(label)}</div>'
                    f'<div class="sel-subtitle">Selecciona equip</div>'
                    f'<div class="cat-grid">{team_cards}</div>'
                    f'</div>'
                )

        active_cls = " active" if is_default else ""
        team_blocks.append(
            f'<div class="season-teams{active_cls}" data-season="{sid}">'
            f'{team_panels_html}'
            f'</div>'
        )

        # --- Screen 3: Build JSON data + detail shells for this season ---
        for entry in entries:
            tid = entry["tournament_id"]
            team = entry["team"]
            team_ids = entry["team_ids"]
            cat_id = f"s{sid}-{slug(entry['tournament_name'])}-{slug(entry['club_id'])}"
            entry_id = f"s{sid}-{slug(entry['tournament_name'] + '-' + team['name'])}"
            num_teams = len([e for e in tournaments_map[tid]["entries"] if e["club_id"] == entry["club_id"]])

            # Build JSON for this entry
            matches_json = []
            seen_match_ids = set()
            for m in entry["all_matches"]:
                if m["id"] in seen_match_ids:
                    continue
                seen_match_ids.add(m["id"])
                hs_val, as_val = match_score(m)
                matches_json.append({
                    "d": m.get("date"),           # UTC cru "YYYY-MM-DD HH:MM:SS"
                    "dl": m.get("date_local"),     # ISO amb TZ Europe/Madrid (preferit pel JS)
                    "ts": m.get("date_ts"),        # epoch (per ordenar)
                    "f": m.get("finished"),
                    "h": m.get("home_team"), "a": m.get("away_team"),
                    "hs": hs_val, "as": as_val,
                    "rn": m.get("round_name", ""),
                    "gn": m.get("group_name", ""),
                    "v": m.get("venue", ""),
                })

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

            # Collect rosters into global flat dict
            for t_id in all_team_ids_set | team_ids:
                if t_id not in all_rost:
                    roster = entry["rosters"].get(t_id, [])
                    if roster:
                        all_rost[t_id] = [{"fn": p["first_name"], "ln": p["last_name"],
                                           "bd": p.get("birthdate", ""), "ro": p["role"]}
                                          for p in roster]

            all_wp[entry_id] = {
                "tid": tid, "tname": entry["tournament_name"],
                "label": short_category(entry["tournament_name"]),
                "dt": team["id"],
                "teams": {k: v for k, v in entry["team_names"].items() if k in all_team_ids_set or k in team_ids},
                "groups": groups_json, "matches": matches_json,
            }

            # Build team selector options
            team_options = []
            for g in entry["all_groups"]:
                for s in g["standings"]:
                    s_id = str(s["id"])
                    if s_id not in [t[0] for t in team_options]:
                        selected = " selected" if s_id == team["id"] else ""
                        team_options.append((s_id, f'<option value="{s_id}"{selected}>{escape(s["name"])}</option>'))

            selector_html = "".join(t[1] for t in team_options)

            detail_section = (
                f'<div class="detail-category" id="{entry_id}" data-entry-id="{entry_id}" '
                f'data-club="{escape(entry["club_id"])}" '
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
            all_detail_sects.append(detail_section)

    # Serialize data for embedding
    wp_json = json.dumps(all_wp, ensure_ascii=False, separators=(',', ':'))
    rost_json = json.dumps(all_rost, ensure_ascii=False, separators=(',', ':'))
    seasons_json_str = json.dumps(seasons_json, ensure_ascii=False, separators=(',', ':'))

    # Season selector (only if multiple seasons)
    season_selector_html = ""
    if len(all_season_data) > 1:
        season_selector_html = (
            f'<div class="season-select-wrap">'
            f'<select id="season-select" class="flow-select" onchange="switchSeason(this.value)">'
            f'{season_options_html}'
            f'</select></div>'
        )
    else:
        # Keep a stable control even with a single season to preserve step flow.
        only_sid = next(iter(all_season_data))
        only_label = escape(all_season_data[only_sid]["label"])
        season_selector_html = (
            f'<div class="season-select-wrap">'
            f'<select id="season-select" class="flow-select" onchange="switchSeason(this.value)">'
            f'<option value="{only_sid}" selected>{only_label}</option>'
            f'</select></div>'
        )

    club_selector_html = (
        '<div class="season-select-wrap">'
        '<select id="club-select" class="flow-select" onchange="switchClub(this.value)">'
        '<option value="">Selecciona club</option>'
        '</select>'
        '</div>'
    )

    category_selector_html = (
        '<div class="season-select-wrap">'
        '<select id="category-select" class="flow-select" disabled onchange="switchCategory(this.value)">'
        '<option value="">Selecciona categoria</option>'
        '</select>'
        '</div>'
    )

    team_selector_html = (
        '<div class="season-select-wrap">'
        '<select id="team-select" class="flow-select" disabled onchange="switchTeamFromSelect(this)">'
        '<option value="">Selecciona equip</option>'
        '</select>'
        '</div>'
    )

    html = (
        '<!DOCTYPE html><html lang="ca"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        f'<title>Waterpolo Tracker</title>'
        f'<style>{CSS}</style></head><body>'
        f'<header><div class="header-inner">'
        f'<div><h1>&#127937; Waterpolo Tracker</h1>'
        f'<div class="subtitle">{total_cats_default} categories</div>'
        f'</div></div></header>'
        f'<main>'
        # Screen 1: Categories
        f'<div id="selection-screen">'
        f'<div class="selection-hero">'
        f'<div class="selection-eyebrow">Vista general</div>'
        f'<div class="sel-title">Flux d\'entrada</div>'
        f'<div class="sel-subtitle">1) Temporada, 2) Club, 3) Categoria, 4) Equip.</div>'
        f'<div class="selection-flow">'
        f'<div class="flow-step"><span class="flow-step-k">Pas 1 · Temporada</span><div class="flow-step-v">{season_selector_html}</div></div>'
        f'<div class="flow-step"><span class="flow-step-k">Pas 2 · Club</span><div class="flow-step-v">{club_selector_html}</div></div>'
        f'<div class="flow-step"><span class="flow-step-k">Pas 3 · Categoria</span><div class="flow-step-v">{category_selector_html}</div></div>'
        f'<div class="flow-step"><span class="flow-step-k">Pas 4 · Equip</span><div class="flow-step-v">{team_selector_html}</div></div>'
        f'</div>'
        f'<div class="selection-actions"><button class="btn-secondary" onclick="showPlayers()">Menu estadistiques jugadors</button></div>'
        f'</div>'
        # Hidden data stores used by dropdown-only flow.
        f'<div id="cat-data-store" style="display:none">{"".join(cat_blocks)}</div>'
        f'<div id="team-data-store" style="display:none">{"".join(team_blocks)}</div>'
        f'</div>'
        # Screen 1B: Player explorer
        f'<div id="player-screen" style="display:none">'
        f'<div class="back-bar"><button class="btn-back" onclick="showCategories()">&#8249; Tornar</button>'
        f'<span class="back-label">Estadistiques de jugadors</span></div>'
        f'<div class="player-search-wrap"><input type="text" id="player-search-input" class="search-input" placeholder="Buscar jugador o staff..." oninput="renderPlayerExplorer(this.value)" autocomplete="off">'
        f'<button class="search-clear" style="display:block" onclick="playerClearSearch()">&times;</button></div>'
        f'<div class="player-screen-grid">'
        f'<div id="player-list" class="player-list"></div>'
        f'<div id="player-detail" class="player-detail"><div class="player-detail-empty">Selecciona un jugador per veure la fitxa.</div></div>'
        f'</div>'
        f'</div>'
        # Screen 3: Detail
        f'<div id="detail-screen" style="display:none">'
        f'<div class="back-bar" id="detail-back-bar"><button class="btn-back" id="detail-back-btn">&#8249; Tornar</button>'
        f'<span class="back-label" id="detail-back-label"></span></div>'
        f'{"".join(all_detail_sects)}'
        f'</div>'
        f'</main>'
        f'<footer>Actualitzat: {build_time}<br>'
        'Dades de <a href="https://actawp.natacio.cat/">Federacio Catalana de Natacio</a> '
        'via <a href="https://clupik.pro">Clupik</a> (API Leverade)<br>'
        'Generat automaticament - <a href="https://github.com/vinner21/water_follow">GitHub</a></footer>'
        f'<script>window.WP={wp_json};window.ROST={rost_json};window.CLUPIK="{clupik}";'
        f'window.SEASONS={seasons_json_str};window.CUR_SEASON="{default_season}";window.CUR_CLUB="";</script>'
        f'<script>{JS}</script></body></html>'
    )
    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(refresh_rosters=False):
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path) as f:
        config = json.load(f)

    if refresh_rosters:
        print("*** ROSTER REFRESH enabled – will re-fetch all team rosters from API ***")
    else:
        print("Rosters: using cache (pass --refresh-rosters to update)")

    # club_id is optional in multiclub mode; kept for backwards compatibility.
    club_id = config.get("club_id")
    manager_id = config["manager_id"]

    # Step 1: Discover all seasons from manager endpoint
    print("=" * 60)
    print("STEP 1: Discovering seasons")
    print("=" * 60)
    seasons_raw = discover_seasons(manager_id)

    for sid, sinfo in seasons_raw.items():
        status_str = "CURRENT" if sinfo["has_in_progress"] else "finished"
        print(f"  Season {sid}: {len(sinfo['tournaments'])} tournaments ({status_str})")

    # Merge multiple "current" season IDs into the one with the most tournaments.
    current_sids = [sid for sid, sinfo in seasons_raw.items() if sinfo["has_in_progress"]]
    if len(current_sids) > 1:
        primary = max(current_sids, key=lambda s: len(seasons_raw[s]["tournaments"]))
        for sid in current_sids:
            if sid != primary:
                print(f"  Merging current season {sid} ({len(seasons_raw[sid]['tournaments'])} tournaments) "
                      f"into {primary} ({len(seasons_raw[primary]['tournaments'])} tournaments)")
                seasons_raw[primary]["tournaments"].extend(seasons_raw[sid]["tournaments"])
                del seasons_raw[sid]

    # Step 2: For each season, load from cache or fetch from API
    print(f"\n{'=' * 60}")
    print("STEP 2: Loading/fetching season data")
    print("=" * 60)

    all_season_data = OrderedDict()

    for sid, sinfo in seasons_raw.items():
        is_current = sinfo["has_in_progress"]

        # Try cache for finished seasons
        if not is_current:
            cached = load_season_cache(sid)
            if cached:
                season_label = cached["season_label"]
                categories_data = cached["tournaments"]
                start_year = int(season_label[:4]) if season_label[:4].isdigit() else datetime.now().year
                cat_age = build_category_age(start_year)
                all_season_data[sid] = {
                    "label": season_label,
                    "status": "finished",
                    "categories_data": categories_data,
                    "category_age": cat_age,
                    "refreshed_at": cached.get("refreshed_at", ""),
                    "age_ref_date": f"{start_year + 1}-12-31",
                }
                if not all_season_data[sid]["refreshed_at"]:
                    cache_path = os.path.join(DATA_DIR, f"{sid}.json")
                    if os.path.exists(cache_path):
                        mtime = os.path.getmtime(cache_path)
                        all_season_data[sid]["refreshed_at"] = datetime.fromtimestamp(mtime).strftime("%d/%m/%Y %H:%M")
                continue

        # Need to discover teams and fetch data from API
        print(f"\n  Fetching season {sid} from API...")

        api_tournaments = []
        cached_categories = []
        for t in sinfo["tournaments"]:
            if t["api_status"] == "finished":
                cached = load_tournament_cache(t["id"])
                if cached:
                    print(f"    Loaded finished tournament {t['name']} from cache")
                    cached_categories.append(cached)
                    continue
            api_tournaments.append(t)

        if cached_categories:
            print(f"  {len(cached_categories)} finished tournaments loaded from cache")
        print(f"  {len(api_tournaments)} tournaments need API calls")

        tournaments_with_us = discover_club_tournaments(api_tournaments, club_id)

        if not tournaments_with_us and not cached_categories:
            print(f"  No tournaments with teams found in season {sid}")
            continue

        # Collect data for each tournament (API-fetched only)
        categories_data = list(cached_categories)
        for t in tournaments_with_us:
            print(f"\n  Collecting data for: {t['name']}")
            try:
                cat_data = collect_tournament_data(t, refresh_rosters=refresh_rosters,
                                                   is_current_season=is_current)
                if cat_data["groups"]:
                    categories_data.append(cat_data)
                    print(f"    -> {len(cat_data['matches'])} matches, {len(cat_data['groups'])} group(s)")
                    if t["api_status"] == "finished":
                        save_tournament_cache(t["id"], cat_data)
                else:
                    print(f"    -> No groups found, skipping")
            except Exception as e:
                print(f"    -> ERROR: {e}")
                continue

        if not categories_data:
            continue

        season_label, start_year = infer_season_info(categories_data)
        cat_age = build_category_age(start_year)

        all_season_data[sid] = {
            "label": season_label,
            "status": "current" if is_current else "finished",
            "categories_data": categories_data,
            "category_age": cat_age,
            "refreshed_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "age_ref_date": datetime.now().strftime("%Y-%m-%d") if is_current else f"{start_year + 1}-12-31",
        }

        if not is_current:
            save_season_cache(sid, season_label, categories_data)
            cleanup_tournament_caches()
            print(f"\n  Cached season {season_label} for future builds")

    if not all_season_data:
        print("No season data found.")
        sys.exit(1)

    # Deduplicate seasons that resolved to the same label
    seen_labels = {}
    duplicates_to_remove = []
    for sid, sdata in all_season_data.items():
        label = sdata["label"]
        if label in seen_labels:
            prev_sid = seen_labels[label]
            prev = all_season_data[prev_sid]
            if len(sdata["categories_data"]) > len(prev["categories_data"]):
                sdata["categories_data"].extend(prev["categories_data"])
                if prev["status"] == "current":
                    sdata["status"] = "current"
                duplicates_to_remove.append(prev_sid)
                seen_labels[label] = sid
            else:
                prev["categories_data"].extend(sdata["categories_data"])
                if sdata["status"] == "current":
                    prev["status"] = "current"
                duplicates_to_remove.append(sid)
        else:
            seen_labels[label] = sid
    for dup_sid in duplicates_to_remove:
        print(f"  Merged duplicate season label '{all_season_data[dup_sid]['label']}' (season {dup_sid})")
        del all_season_data[dup_sid]

    # Sort seasons: current first, then by label descending
    current = [(sid, sd) for sid, sd in all_season_data.items() if sd["status"] == "current"]
    finished = [(sid, sd) for sid, sd in all_season_data.items() if sd["status"] != "current"]
    finished.sort(key=lambda x: x[1]["label"], reverse=True)
    all_season_data = OrderedDict(current + finished)

    # Step 3: Generate HTML
    print(f"\n{'=' * 60}")
    print("STEP 3: Generating HTML")
    print("=" * 60)

    html = generate_html(all_season_data, config)
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
            "-p", os.environ.get("STATICRYPT_PASSWORD", "posahi_un_password"),
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

    # Summary
    print(f"\n{'=' * 60}")
    print(f"Site generated: {out_path}")
    print(f"Seasons: {len(all_season_data)}")
    for sid, sdata in all_season_data.items():
        total_matches = sum(len(c['matches']) for c in sdata['categories_data'])
        cats = len(set(c['tournament_name'] for c in sdata['categories_data']))
        status = "EN CURS" if sdata['status'] == 'current' else "tancada"
        print(f"  {sdata['label']} ({status}): {cats} categories, {total_matches} partits")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Build Water Polo Tracker")
    parser.add_argument("--refresh-rosters", action="store_true",
                        help="Re-fetch all team rosters from API (expensive, ~400 calls). "
                             "Without this flag, cached rosters are used.")
    args = parser.parse_args()
    main(refresh_rosters=args.refresh_rosters)
