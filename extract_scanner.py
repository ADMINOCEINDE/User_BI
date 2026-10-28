"""Extraction bulk via Scanner API -> RAW_WORKSPACE_USERS, RAW_REPORTS,
RAW_REPORT_USERS, RAW_DATASETS, RAW_DASHBOARDS, RAW_DATAFLOWS"""

import time

import pandas as pd
import requests

from config import PBI_BASE, pbi_headers, api_get, log


def _scan_batches(workspace_ids: list[str]) -> list[dict]:
    """Scanner API getInfo par batch de 100 workspaces."""
    all_workspaces = []
    headers = pbi_headers()

    for i in range(0, len(workspace_ids), 100):
        batch = workspace_ids[i:i+100]
        log.info(f"  Scan batch {i//100 + 1} ({len(batch)} workspaces)...")

        r = requests.post(
            f"{PBI_BASE}/admin/workspaces/getInfo"
            f"?lineage=False&datasourceDetails=False&datasetSchema=False"
            f"&datasetExpressions=False&getArtifactUsers=True",
            headers=headers,
            json={"workspaces": batch},
            timeout=60,
        )
        r.raise_for_status()
        scan_id = r.json()["id"]

        for _ in range(60):
            r = api_get(f"{PBI_BASE}/admin/workspaces/scanStatus/{scan_id}", headers)
            if r.json()["status"] == "Succeeded":
                break
            time.sleep(2)

        r = api_get(f"{PBI_BASE}/admin/workspaces/scanResult/{scan_id}", headers)
        all_workspaces.extend(r.json().get("workspaces", []))

    return all_workspaces


def extract(workspace_ids: list[str]) -> dict[str, pd.DataFrame]:
    log.info("Extraction Scanner API (bulk)...")
    scan_data = _scan_batches(workspace_ids)

    ws_users, reports, report_users = [], [], []
    datasets, dashboards, dataflows = [], [], []

    for ws in scan_data:
        gid = ws["id"]

        for u in ws.get("users", []):
            u["groupId"] = gid
            ws_users.append(u)

        for rep in ws.get("reports", []):
            for u in rep.get("users", []):
                u["reportId"] = rep.get("id")
                u["groupId"] = gid
                report_users.append(u)
            rep.pop("users", None)
            rep["groupId"] = gid
            reports.append(rep)

        for ds in ws.get("datasets", []):
            ds.pop("users", None)
            ds["groupId"] = gid
            datasets.append(ds)

        for db in ws.get("dashboards", []):
            db.pop("users", None)
            db["groupId"] = gid
            dashboards.append(db)

        for df_item in ws.get("dataflows", []):
            df_item.pop("users", None)
            df_item["groupId"] = gid
            dataflows.append(df_item)

    log.info(f"  {len(ws_users)} workspace-users, {len(reports)} reports, "
             f"{len(report_users)} report-users, {len(datasets)} datasets, "
             f"{len(dashboards)} dashboards, {len(dataflows)} dataflows")

    return {
        "workspace_users": pd.json_normalize(ws_users),
        "reports": pd.json_normalize(reports),
        "report_users": pd.json_normalize(report_users),
        "datasets": pd.json_normalize(datasets),
        "dashboards": pd.json_normalize(dashboards),
        "dataflows": pd.json_normalize(dataflows),
    }
