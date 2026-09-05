"""Layout + routing tests for split-screen search briefing."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from executor.search_briefing import format_found_about, is_ad_search_result, split_geometry
from brain.router import parse_time_query, route_command


def check(label, cond):
    if not cond:
        raise AssertionError(label)
    print("OK", label)


geo = split_geometry({"x": 0, "y": 0, "width": 1920, "height": 1080})
check("friday is left", geo["friday"]["x"] == 0)
check("browser is right of friday", geo["browser"]["left"] > geo["friday"]["width"])
check("no overlap", geo["browser"]["left"] >= geo["friday"]["width"])
check("fills height", geo["friday"]["height"] == 1080 and geo["browser"]["height"] == 1080)
check("browser reasonably wide", geo["browser"]["width"] >= 480)

intent, params = route_command("search for donald trump")
check("search for -> search_browser", intent == "search_browser")
check("search query", "trump" in (params or {}).get("query", "").lower())

intent, params = route_command("look up mitochondria")
check("look up -> smart_search briefing", intent == "smart_search")

intent, params = route_command("about Donald Trump on the internet")
check("on the internet -> smart_search briefing", intent == "smart_search")

intent, params = route_command("Can you just search for Donald Trump?")
check("can you just search for -> search_browser", intent == "search_browser")
check(
    "can you just search query",
    "trump" in (params or {}).get("query", "").lower(),
)

long_bio = (
    "Here's what I found: Donald John Trump is an American politician, media personality, "
    "and businessman who is the 47th president of the United States. A member of the "
    "Republican Party, he served as the 45th president from 2017 to 2021. Born into a "
    "wealthy New York City family, Trump graduated from the University of Pennsylvania."
)
spoken = format_found_about("Donald Trump", long_bio)
check("opens with found about query", spoken.startswith("Here's what I found about Donald Trump."))
check("does not dump the full bio", "University of Pennsylvania" not in spoken)
check("points to the screen", "screen" in spoken.lower())

intent, params = route_command("Time in Tokyo")
check("time in tokyo is time", intent == "time")
check("tokyo timezone", (params or {}).get("timezone") == "Asia/Tokyo")

intent, params = route_command("Tokyo time")
check("tokyo time is time", intent == "time" and (params or {}).get("timezone") == "Asia/Tokyo")

intent, params = route_command("what time is it in London")
check("what time in london", intent == "time" and (params or {}).get("timezone") == "Europe/London")

check("parse time in tokyo", (parse_time_query("Time in Tokyo") or {}).get("timezone") == "Asia/Tokyo")
check(
    "booking ad filtered",
    is_ad_search_result({
        "title": "Hotels, Homes, And Everything In Between",
        "url": "https://www.booking.com/city/jp/tokyo.html",
        "snippet": "Choose From A Wide Range Of Properties",
    }),
)

print("\nAll search briefing tests passed.")
