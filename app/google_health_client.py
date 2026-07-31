import time
from urllib.parse import urlencode

import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from app.models import save_fitbit_token
from config import get_current_config


SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.irn.readonly",
    "https://www.googleapis.com/auth/googlehealth.ecg.readonly",
    "https://www.googleapis.com/auth/googlehealth.location.readonly",
]


def get_permission_screen_url(user_state):
    config = get_current_config()

    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": config.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": user_state,
    }

    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


def do_google_auth(code, user_id):
    config = get_current_config()

    token_url = "https://oauth2.googleapis.com/token"

    data = {
        "code": code,
        "client_id": config.GOOGLE_CLIENT_ID,
        "client_secret": config.GOOGLE_CLIENT_SECRET,
        "redirect_uri": config.GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }

    response = requests.post(token_url, data=data)

    if response.status_code != 200:
        raise RuntimeError("Token exchange failed: {}".format(response.text))

    token_data = response.json()

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)
    expires_at = time.time() + expires_in

    if not refresh_token:
        raise RuntimeError(
            "Google did not return a refresh token. Delete this app from your Google account permissions "
            "and try again."
        )

    return save_fitbit_token(user_id, access_token, refresh_token, expires_at)

def get_google_credentials(saved_token):
    config = get_current_config()

    if not saved_token.refresh_token:
        raise RuntimeError(
            "No refresh token was saved for this user. "
            "You need to delete the local database and re-authorize this Google account."
        )

    if not config.GOOGLE_CLIENT_ID:
        raise RuntimeError(
            "GOOGLE_CLIENT_ID is missing. Set it in PowerShell before running Flask."
        )

    if not config.GOOGLE_CLIENT_SECRET:
        raise RuntimeError(
            "GOOGLE_CLIENT_SECRET is missing. Set it in PowerShell before running Flask."
        )

    credentials = Credentials(
        token=saved_token.access_token,
        refresh_token=saved_token.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )

    token_expires_at = float(saved_token.expires_at or 0)
    token_is_expired = time.time() > (token_expires_at - 60)

    if token_is_expired:
        credentials.refresh(Request())

        expires_at = credentials.expiry.timestamp() if credentials.expiry else time.time() + 3600

        save_fitbit_token(
            saved_token.user_id,
            credentials.token,
            credentials.refresh_token or saved_token.refresh_token,
            expires_at
        )

    return credentials


def list_data_points(saved_token, data_type, page_size=1000, max_pages=1000):
    credentials = get_google_credentials(saved_token)

    url = "https://health.googleapis.com/v4/users/me/dataTypes/{}/dataPoints".format(data_type)

    headers = {
        "Authorization": "Bearer {}".format(credentials.token),
        "Accept": "application/json",
    }

    all_data_points = []
    next_page_token = None
    page_count = 0

    while True:
        params = {
            "page_size": page_size
        }

        if next_page_token:
            params["page_token"] = next_page_token

        response = requests.get(url, headers=headers, params=params)

        if response.status_code != 200:
            return {
                "status_code": response.status_code,
                "error": response.text
            }

        data = response.json()
        page_points = data.get("dataPoints", [])
        all_data_points.extend(page_points)

        next_page_token = data.get("nextPageToken")
        page_count += 1

        print("{} page {}: downloaded {} rows, total {}".format(
            data_type,
            page_count,
            len(page_points),
            len(all_data_points)
        ))

        if not next_page_token:
            break

        if page_count >= max_pages:
            print("Stopped {} after {} pages for safety.".format(data_type, max_pages))
            break

    return {
        "dataPoints": all_data_points,
        "nextPageToken": next_page_token
    }