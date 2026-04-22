import requests
from decouple import config

TOKEN_URL = "https://api.gs1br.org/oauth/access-token"

payload = {
    "grant_type": "client_credentials",
    "client_id":     config("GS1_CLIENT_ID"),
    "client_secret": config("GS1_CLIENT_SECRET"),
}
headers = {
    "Content-Type": "application/x-www-form-urlencoded",
    "apikey":       config("GS1_API_KEY"),
    "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

response = requests.post(TOKEN_URL, data=payload, headers=headers, timeout=10)

print("Status:", response.status_code)
print("Body:", response.text)