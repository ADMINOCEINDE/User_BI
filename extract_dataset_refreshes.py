"""Extraction refresh history -> RAW_DATASET_REFRESHES
Note : endpoint non-admin, nécessite que le SP soit membre du workspace."""

import pandas as pd
import requests

from config import PBI_BASE, pbi_headers, api_get, log


def extract() -> pd.DataFrame:
    log.info("Extraction refresh history...")
    headers = pbi_headers()

    # Workspaces accessibles au SP (endpoint non-admin)
    r = api_get(f"{PBI_BASE}/groups", headers)
    sp_workspaces = [ws["id"] for ws in r.json().get("value", [])]
    log.info(f"  {len(sp_workspaces)} workspaces accessibles au SP")

    # Datasets dans ces workspaces
    all_datasets = []
    for gid in sp_workspaces:
        r = api_get(f"{PBI_BASE}/groups/{gid}/datasets", headers)
        for ds in r.json().get("value", []):
            if ds.get("isRefreshable"):
                ds["groupId"] = gid
                all_datasets.append(ds)
    log.info(f"  {len(all_datasets)} datasets refreshables")

    # Refreshes
    all_rows = []
    for i, ds in enumerate(all_datasets):
        try:
            r = api_get(
                f"{PBI_BASE}/groups/{ds['groupId']}/datasets/{ds['id']}/refreshes?$top=10",
                headers,
            )
            for ref in r.json().get("value", []):
                ref["datasetId"] = ds["id"]
                ref["datasetName"] = ds.get("name")
                ref["groupId"] = ds["groupId"]
                all_rows.append(ref)
        except requests.HTTPError:
            pass
        if (i + 1) % 25 == 0:
            log.info(f"  {i + 1}/{len(all_datasets)}...")

    log.info(f"  {len(all_rows)} refreshes")
    return pd.json_normalize(all_rows)
