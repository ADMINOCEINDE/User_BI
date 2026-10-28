"""Extraction des apps + users -> RAW_APPS, RAW_APP_USERS"""

import pandas as pd
import requests

from config import PBI_BASE, pbi_headers, pbi_get_all, api_get, log


def extract() -> tuple[pd.DataFrame, pd.DataFrame]:
    log.info("Extraction apps...")
    apps = pbi_get_all("admin/apps?$top=5000")
    log.info(f"  {len(apps)} apps")

    log.info("Extraction users par app...")
    headers = pbi_headers()
    all_users = []
    for a in apps:
        try:
            r = api_get(f"{PBI_BASE}/admin/apps/{a['id']}/users", headers)
            for u in r.json().get("value", []):
                u["appId"] = a["id"]
                all_users.append(u)
        except requests.HTTPError:
            pass
    log.info(f"  {len(all_users)} droits app-user")

    return pd.json_normalize(apps), pd.json_normalize(all_users)
