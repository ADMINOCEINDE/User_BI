"""Extraction des capacités -> RAW_CAPACITIES"""

import pandas as pd
from config import pbi_get_all, log


def extract() -> pd.DataFrame:
    log.info("Extraction capacités...")
    rows = pbi_get_all("admin/capacities")
    log.info(f"  {len(rows)} capacités")
    return pd.json_normalize(rows)
