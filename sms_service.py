# sms_service.py
import requests
from config import SEMAPHORE_API_KEY, SEMAPHORE_SENDER, SMS_ENABLED

def send_sms(number, message):
    
    if not SMS_ENABLED:
        print(f"[FAKE SMS to {number}]: {message}")
        return True

    url = "https://api.semaphore.co/api/v4/messages"
    payload = {
        'apikey': SEMAPHORE_API_KEY,
        'number': number, 
        'message': message,
        'sendername': SEMAPHORE_SENDER
    }
    try:
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code == 200:
            print(f"[SMS SENT to {number}]")
            return True
        else:
            print(f"[SMS FAILED]: {response.text}") 
            return False
    except:
        print("[SMS FAILED]: No Internet")
        return False