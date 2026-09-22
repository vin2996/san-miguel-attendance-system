import os
import requests

IPROG_API_TOKEN = os.environ.get("IPROG_API_TOKEN", "YOUR_IPROG_API_TOKEN_HERE")
IPROG_API_URL = "https://www.iprogsms.com/api/v1/sms_messages"

try:
    from config import SMS_ENABLED
except ImportError:
    SMS_ENABLED = True

def send_sms(number, message):
    if not SMS_ENABLED:
        print(f"[FAKE SMS to {number}]: {message}")
        return True
    if not number:
        return False
    p = number.strip()
    if p.startswith("+"):
        p = p[1:]
    if p.startswith("0"):
        p = "63" + p[1:]
    payload = {
        "api_token": IPROG_API_TOKEN,
        "phone_number": p,
        "message": message
    }
    try:
        r = requests.post(IPROG_API_URL, json=payload, timeout=15)
        if r.status_code == 200:
            print(f"[IPROG SENT to {p}] {r.text}")
            return True
        else:
            print(f"[IPROG FAILED]: {r.text}")
            return False
    except Exception as e:
        print(f"[IPROG FAILED]: {e}")
        return False
