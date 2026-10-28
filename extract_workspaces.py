"""Extraction des workspaces actifs -> RAW_WORKSPACES"""

import pandas as pd
from config import pbi_get_all, log


def extract() -> pd.DataFrame:
    log.info("Extraction workspaces...")
    rows = pbi_get_all("admin/groups?$top=5000&$filter=state eq 'Active'")
    log.info(f"  {len(rows)} workspaces")
    return pd.json_normalize(rows)
