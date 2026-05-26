import os
import requests


def send_whatsapp(phone: str, message: str):
    api_key = os.getenv("FONNTE_API_KEY")
    if not api_key:
        return
    requests.post(
        "https://api.fonnte.com/send",
        json={"target": phone, "message": message},
        headers={"Authorization": api_key},
        timeout=10,
    )
