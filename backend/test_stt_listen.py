"""STT correction, phantom filter, and greeting routing."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stt.correct import correct_transcript
from stt.filter import is_phantom_transcript
from brain.router import route_command


def check(label, cond):
    if not cond:
        raise AssertionError(label)
    print("OK", label)


check("harvis -> friday", correct_transcript("Hi, I'm Harvis.") == "hi friday")
check("jervis -> friday", "friday" in correct_transcript("hey jervis").lower())

fixed = correct_transcript("Hi, I'm Harvis.")
intent, _ = route_command(fixed)
check("hi i'm harvis is greeting", intent == "greeting")

intent, _ = route_command("Hi Jadwish")
check("hi jadwish is greeting", intent == "greeting")

check("give me a minute is phantom", is_phantom_transcript("Give me a minute"))
check("that means give me a minute is phantom", is_phantom_transcript("That means, give me a minute."))
check("real command not phantom", not is_phantom_transcript("search for donald trump"))

print("\nAll STT listen tests passed.")
