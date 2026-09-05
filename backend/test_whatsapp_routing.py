"""Router / contact-resolve tests. Does not send any WhatsApp messages."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from brain.router import route_command
from executor.automation import resolve_whatsapp_number


def check(label, cond):
    if not cond:
        raise AssertionError(label)
    print("OK", label)


cases = [
    ("Open Watsapp and send Satish. Bye.", "satish", "bye"),
    ("open whatsapp and send Satish hi. Satish.", "satish", "hi"),
    ("open whatsapp and send Sadeesh hi", "sadeesh", "hi"),
    ("open whatsapp and send sathish hello from friday", "sathish", "hello from friday"),
    ("send message to sathish hi friday test", "sathish", "hi friday test"),
    ("send sathish hello", "sathish", "hello"),
    ("open whatsapp and send Satish", "satish", ""),
    ("whatsapp sathish", "sathish", ""),
    ("open whatsapp and send Satish hi", "satish", "hi"),
    ("send hi to sathish", "sathish", "hi"),
]

for text, name, message in cases:
    intent, params = route_command(text)
    check(f"{text!r} -> send_whatsapp", intent == "send_whatsapp")
    check(f"{text!r} name={params.get('name')!r}", (params or {}).get("name", "").lower() == name)
    check(f"{text!r} message={params.get('message')!r}", (params or {}).get("message", "") == message)

intent, params = route_command("open whatsapp")
check("open whatsapp is not a send", intent != "send_whatsapp")

ok, phone, label, err = resolve_whatsapp_number("sathish")
check("resolve sathish", ok and phone.endswith("8519929108"))

ok, phone, label, err = resolve_whatsapp_number("Sadeesh")
check(f"resolve Sadeesh -> {phone} {err}", ok and phone.endswith("8519929108"))

ok, phone, label, err = resolve_whatsapp_number("Satish", "+91...")
check("ignore placeholder +91...", ok and phone.endswith("8519929108"))

ok, phone, label, err = resolve_whatsapp_number("Satish", number="+91...")
check("ignore explicit placeholder", ok and phone.endswith("8519929108"))

ok, phone, label, err = resolve_whatsapp_number("everyone")
check("block everyone", not ok)

ok, phone, label, err = resolve_whatsapp_number("the group")
check("block group target", not ok)

all_ok = [
    ("send hello from friday to all contacts", "hello from friday"),
    ("send to all contacts good morning", "good morning"),
    ("open whatsapp and send all contacts hello there", "hello there"),
    ("message all my contacts I am friday", "i am friday"),
]
for text, expected in all_ok:
    intent, params = route_command(text)
    check(f"{text!r} -> send_whatsapp_all", intent == "send_whatsapp_all")
    check(
        f"{text!r} message={params.get('message')!r}",
        (params or {}).get("message", "").lower() == expected,
    )

unsafe = [
    "send hello to everyone",
    "send this to the group",
    "send hello to all",
    "broadcast this",
]
for text in unsafe:
    intent, params = route_command(text)
    check(
        f"{text!r} is not all-contacts",
        intent != "send_whatsapp_all" or (params or {}).get("error"),
    )

intent, params = route_command("remember that wifi password is hunter2")
check("remember routes", intent == "remember")

intent, params = route_command("what do you remember about wifi password")
check("recall routes", intent == "recall")

intent, params = route_command("What is mitochondria?")
check("what is mitochondria is qa", intent == "qa")

intent, params = route_command("What is TagGPT?")
check("what is TagGPT is qa", intent == "qa")

intent, params = route_command("What is chai jpd?")
check("what is chai jpd is qa", intent == "qa")

intent, params = route_command("about Donald Trump on the internet")
check(
    "about X on the internet is smart_search",
    intent == "smart_search" and "trump" in (params or {}).get("query", "").lower(),
)

intent, params = route_command("look up mitochondria")
check("look up is smart_search", intent == "smart_search")

intent, params = route_command("search for donald trump")
check("search for is search_browser", intent == "search_browser")

intent, params = route_command("play music")
check("play music is local", intent == "play_local_music")

intent, params = route_command("Hi Jadwish")
check("Hi Jadwish is greeting", intent == "greeting")

intent, params = route_command("Hi, I'm Friday")
check("hi i'm friday is greeting", intent == "greeting")

intent, params = route_command("what is my wifi password")
check("what is my ... is recall", intent == "recall")

intent, params = route_command("list contacts")
check("list contacts", intent == "recall" and (params or {}).get("query") == "contacts")

intent, params = route_command("add a task buy milk")
check("add task", intent == "add_task")

print("\nAll routing tests passed.")
