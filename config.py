"""Configuration, authentification MSAL et helpers API."""

import os
import time
import logging

import msal
import requests
from dotenv import load_dotenv

load_dotenv()

TENANT_ID = os.environ["TENANT_ID"]
CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPE_PBI = ["https://analysis.windows.net/powerbi/api/.default"]
SCOPE_GRAPH = ["https://graph.microsoft.com/.default"]

PBI_BASE = "https://api.powerbi.com/v1.0/myorg"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

ACTIVITY_MAX_DAYS = 28

log = logging.getLogger("extract")


def get_token(scope: list[str]) -> str:
    app = msal.ConfidentialClientApplication(
        CLIENT_ID, authority=AUTHORITY, client_credential=CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(scopes=scope)
    if "access_token" not in result:
        raise RuntimeError(f"Token error: {result.get('error_description', result)}")
    return result["access_token"]


def pbi_headers() -> dict:
    return {"Authorization": f"Bearer {get_token(SCOPE_PBI)}"}


def graph_headers() -> dict:
    return {"Authorization": f"Bearer {get_token(SCOPE_GRAPH)}"}


def api_get(url: str, headers: dict, retries: int = 3) -> requests.Response:
    """GET avec retry automatique sur 429."""
    for _ in range(retries):
        r = requests.get(url, headers=headers, timeout=120)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 5))
            log.warning(f"  429 rate limit, pause {wait}s...")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


def pbi_get_all(path: str, params: dict | None = None) -> list[dict]:
    """GET paginé Power BI (gère @odata.nextLink)."""
    url = f"{PBI_BASE}/{path}"
    headers = pbi_headers()
    rows = []
    while url:
        r = api_get(url, headers)
        data = r.json()
        rows.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
    return rows


def graph_get_all(path: str) -> list[dict]:
    """GET paginé Graph (gère @odata.nextLink)."""
    url = f"{GRAPH_BASE}/{path}"
    headers = graph_headers()
    results = []
    while url:
        r = requests.get(url, headers=headers, timeout=120)
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
    return results
