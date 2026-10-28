"""Extraction des licences tenant Graph -> RAW_GRAPH_LICENSES"""

import pandas as pd
from config import graph_get_all, log


def extract() -> pd.DataFrame:
    log.info("Extraction licences tenant Graph...")
    subs = graph_get_all("subscribedSkus")
    log.info(f"  {len(subs)} SKUs")
    return pd.json_normalize(subs)
