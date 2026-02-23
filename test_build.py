#!/usr/bin/env python3
"""Quick test for cache roundtrip and generate_html with mock data."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
import build

# Test 1: Cache roundtrip
print("Test 1: Cache roundtrip...")
test_data = [{
    "tournament_id": "123",
    "tournament_name": "TEST TOURNAMENT",
    "our_teams": [{"id": "456", "name": "TEST TEAM", "avatar": ""}],
    "our_team_ids": {"456"},
    "groups": [{
        "id": "g1", "name": "Grup A",
        "standings": [{"id": "456", "name": "TEST TEAM", "position": 1,
                       "points": 10, "played": 5, "won": 3, "drawn": 1, "lost": 1,
                       "goals_for": 20, "goals_against": 10, "goal_diff": 10}],
        "our_team_ids": {"456"},
    }],
    "matches": [{
        "id": "m1", "date": "2024-10-15 18:00:00", "finished": True, "canceled": False,
        "postponed": False, "rest": False, "home_team": "456", "away_team": "789",
        "venue": "Piscina Test",
        "results": [
            {"value": 5, "score": 5, "team_id": "456", "match_id": "m1"},
            {"value": 3, "score": 3, "team_id": "789", "match_id": "m1"},
        ],
        "round_name": "J1", "round_order": 1, "group_id": "g1", "group_name": "Grup A",
    }],
    "team_names": {"456": "TEST TEAM", "789": "OTHER TEAM"},
    "rosters": {"456": [{"first_name": "MARC", "last_name": "GARCIA", "birthdate": "2014-01-01", "role": "player"}]},
}]

build.save_season_cache("test_999", "2024-25", test_data)
loaded = build.load_season_cache("test_999")
assert loaded is not None, "Cache load returned None"
assert loaded["season_label"] == "2024-25"
assert isinstance(loaded["tournaments"][0]["our_team_ids"], set)
assert "456" in loaded["tournaments"][0]["our_team_ids"]
assert isinstance(loaded["tournaments"][0]["groups"][0]["our_team_ids"], set)
os.remove(os.path.join(build.DATA_DIR, "test_999.json"))
print("  PASSED")

# Test 2: generate_html with mock multi-season data
print("Test 2: generate_html with mock data...")
from collections import OrderedDict
mock_seasons = OrderedDict()
mock_seasons["s1"] = {
    "label": "2025-26",
    "status": "current",
    "categories_data": test_data,
    "category_age": build.build_category_age(2025),
}
mock_seasons["s2"] = {
    "label": "2024-25",
    "status": "finished",
    "categories_data": test_data,
    "category_age": build.build_category_age(2024),
}
config = {"club_id": "456", "clupik_base_url": "https://clupik.pro"}
html = build.generate_html(mock_seasons, config)
assert "window.WP=" in html, "WP data not in HTML"
assert "window.SEASONS=" in html, "SEASONS data not in HTML"
assert "window.CUR_SEASON=" in html, "CUR_SEASON not in HTML"
assert "season-select" in html, "Season selector not in HTML"
assert 'data-season="s1"' in html, "Season s1 block not in HTML"
assert 'data-season="s2"' in html, "Season s2 block not in HTML"
assert "switchSeason" in html, "switchSeason JS not in HTML"
assert "2025-26" in html, "Season label 2025-26 not in HTML"
assert "2024-25" in html, "Season label 2024-25 not in HTML"
# Check season-prefixed entry IDs
assert "ss1-" in html, "Season-prefixed IDs for s1 not found"
assert "ss2-" in html, "Season-prefixed IDs for s2 not found"
print(f"  PASSED (HTML size: {len(html)} bytes)")

# Test 3: infer_season_info
print("Test 3: infer_season_info...")
label, year = build.infer_season_info(test_data)
assert label == "2024-25", f"Expected 2024-25, got {label}"
assert year == 2024, f"Expected 2024, got {year}"
print("  PASSED")

print("\nAll tests passed!")
