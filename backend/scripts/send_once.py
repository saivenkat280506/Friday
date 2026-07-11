"""One-off live WhatsApp send."""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)

os.environ.pop("WHATSAPP_DRY_RUN", None)
os.environ.pop("WHATSAPP_FAST", None)

from executor.whatsapp_handler import send_whatsapp_message_sync

CONTACT = sys.argv[1] if len(sys.argv) > 1 else "sathish"
MESSAGE = sys.argv[2] if len(sys.argv) > 2 else (
    "iam friday ai mr.stark's personal assistant how may I help you today?"
)

if __name__ == "__main__":
    print(f"Sending to {CONTACT!r}...")
    result = send_whatsapp_message_sync(CONTACT, MESSAGE)
    print(result)