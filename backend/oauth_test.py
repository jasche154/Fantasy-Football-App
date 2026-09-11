from dotenv import load_dotenv
import os
import urllib.parse

load_dotenv()

CLIENT_ID = os.getenv("YAHOO_CLIENT_ID")
REDIRECT_URI = os.getenv("YAHOO_REDIRECT_URI")

AUTH_BASE_URL = "https://api.login.yahoo.com/oauth2/request_auth"

params = {
    "client_id": CLIENT_ID,
    "redirect_uri": REDIRECT_URI,
    "response_type": "code",
    "scope": "fspt-r",  # fantasy sports, read-only
}

auth_url = f"{AUTH_BASE_URL}?{urllib.parse.urlencode(params)}"

print("Go to this URL and log in:")
print(auth_url)