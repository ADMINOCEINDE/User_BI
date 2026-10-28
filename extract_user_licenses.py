"""Extraction des licences par utilisateur Graph -> RAW_USER_LICENSES"""

import pandas as pd
from config import graph_get_all, log


def extract() -> pd.DataFrame:
    log.info("Extraction licences par utilisateur Graph...")
    users = graph_get_all("users?$select=id,userPrincipalName,assignedLicenses")
    rows = []
    for u in users:
        for lic in u.get("assignedLicenses", []):
            rows.append({
                "userId": u["id"],
                "userPrincipalName": u.get("userPrincipalName"),
                "skuId": lic.get("skuId"),
            })
    log.info(f"  {len(rows)} assignations")
    return pd.json_normalize(rows)
