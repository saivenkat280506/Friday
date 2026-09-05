"""Local memory store tests. No WhatsApp, no network."""
import os
import sys
import tempfile

tmp = os.path.join(tempfile.gettempdir(), "friday_memory_test.db")
os.environ["FRIDAY_MEMORY_DB"] = tmp
if os.path.exists(tmp):
    os.remove(tmp)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from brain.memory_store import (
    init_store,
    remember_fact,
    recall,
    add_task,
    list_tasks,
    complete_task,
    unique_whatsapp_targets,
    parse_remember_text,
    try_fast_answer,
)


def check(label, cond):
    if not cond:
        raise AssertionError(label)
    print("OK", label)


init_store()
key, value = parse_remember_text("that wifi password is hunter2")
check("parse remember", key == "wifi password" and value == "hunter2")

ok, msg = remember_fact("wifi password", "hunter2")
check("remember", ok and "hunter2" in msg)

ok, msg = recall("wifi password")
check("recall exact", ok and "hunter2" in msg)

hit = try_fast_answer("what do you remember about wifi password")
check("fast answer memory hit", hit and "hunter2" in hit)

miss = try_fast_answer("what is mitochondria")
check("fast answer ignores general knowledge", miss is None)

ok, msg = add_task("buy milk")
check("add task", ok)

ok, msg = list_tasks("open")
check("list tasks", ok and "buy milk" in msg)

ok, msg = complete_task("milk")
check("complete task", ok)

ok, msg = list_tasks("open")
check("no open tasks", ok and "no open tasks" in msg.lower())

targets = unique_whatsapp_targets()
phones = {phone for _, phone in targets}
check("unique phones", len(phones) == len(targets))
check("sathish allowlisted", any(name == "sathish" for name, _ in targets))
check("no group names", all("group" not in name for name, _ in targets))

print("\nAll memory tests passed.")
