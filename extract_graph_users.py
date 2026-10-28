"""Extraction des utilisateurs Graph -> RAW_GRAPH_USERS"""

import pandas as pd
from config import graph_get_all, log


def extract() -> pd.DataFrame:
    log.info("Extraction users Graph...")
    users = graph_get_all(
        "users?$select=id,displayName,mail,userPrincipalName,accountEnabled,"
        "userType,createdDateTime,department,companyName,jobTitle"
    )
    log.info(f"  {len(users)} users")
    return pd.json_normalize(users)
