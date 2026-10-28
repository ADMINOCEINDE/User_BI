"""
Orchestrateur - Extraction Power BI Admin API + Microsoft Graph -> Snowflake
Usage : python main.py
"""

import os
import sys
import time
import uuid
import logging
from datetime import datetime, timezone

import pandas as pd
import requests

import extract_workspaces
import extract_scanner
import extract_dataset_refreshes
import extract_capacities
import extract_apps
import extract_activity
import extract_graph_users
import extract_graph_licenses
import extract_user_licenses
from loader import (
    validate_env, sf_connect, load_snapshot, load_upsert,
    init_log_table, write_log, get_last_activity_date,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("extract")


# ── Notification Teams ────────────────────────────────────────────────────────
def _envoyer_teams(results: list, duree: float, run_id: str):
    webhook_url = os.getenv("TEAMS_WEBHOOK_URL")
    if not webhook_url:
        log.warning("TEAMS_WEBHOOK_URL non défini, notification ignorée")
        return

    erreurs = [r for r in results if r["status"] == "ERREUR"]
    nb_erreurs = len(erreurs)
    statut_global = "✅ Succès" if nb_erreurs == 0 else f"⚠️ {nb_erreurs} erreur(s)"
    couleur = "00C176" if nb_erreurs == 0 else "FF0000"

    facts = []
    for r in results:
        if r["status"] == "OK":
            valeur = f"✅ {r['rows']:,} lignes — {r['duration']}s".replace(",", " ")
        else:
            valeur = f"❌ ERREUR — {r['duration']}s"
        facts.append({"name": r["table"], "value": valeur})

    message = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": couleur,
        "summary": "Pipeline Power BI → Snowflake",
        "sections": [
            {
                "activityTitle": f"Pipeline Power BI → Snowflake — {statut_global}",
                "activitySubtitle": (
                    f"🕐 Durée : {duree:.0f} sec"
                    f" | 🔑 run_id : {run_id}"
                    f" | 📅 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
                ),
                "activityImage": "https://img.icons8.com/color/48/snowflake.png",
                "facts": facts,
                "markdown": True,
            }
        ],
    }

    if erreurs:
        detail_erreurs = "\n\n".join(
            f"**{r['table']}** : {r.get('error', 'erreur inconnue')}"
            for r in erreurs
        )
        message["sections"].append(
            {"title": "❌ Détail des erreurs", "text": detail_erreurs}
        )

    try:
        response = requests.post(webhook_url, json=message, timeout=10)
        if response.status_code == 200:
            log.info("✓ Notification Teams envoyée")
        else:
            log.error("✗ Erreur Teams : %s — %s", response.status_code, response.text)
    except Exception as e:
        log.error("✗ Impossible d'envoyer la notification Teams : %s", e)


# ── Étape extract + load ──────────────────────────────────────────────────────
def run_step(conn, run_id: str, name: str, extract_fn, load_fn) -> dict:
    """Exécute une étape extract + load avec timing, logging et isolation d'erreur."""
    started_at = datetime.now(timezone.utc)
    log.info(f"--- {name} ---")
    try:
        t0 = time.time()
        result = extract_fn()
        duration_extract = time.time() - t0

        t1 = time.time()
        row_count = load_fn(result)
        duration_load = time.time() - t1

        duration = duration_extract + duration_load
        finished_at = datetime.now(timezone.utc)

        write_log(conn, run_id, name, "OK", row_count, round(duration, 2),
                  None, started_at.isoformat(), finished_at.isoformat())

        return {"table": name, "status": "OK", "rows": row_count,
                "duration": round(duration, 1)}

    except Exception as e:
        finished_at = datetime.now(timezone.utc)
        duration = (finished_at - started_at).total_seconds()
        error_msg = str(e)[:500]
        log.error(f"  ERREUR {name}: {error_msg}")

        write_log(conn, run_id, name, "ERREUR", 0, round(duration, 2),
                  error_msg, started_at.isoformat(), finished_at.isoformat())

        return {"table": name, "status": "ERREUR", "rows": 0,
                "duration": round(duration, 1), "error": error_msg}


# ── Point d'entrée ────────────────────────────────────────────────────────────
def main():
    total_start = time.time()
    run_id = str(uuid.uuid4())[:8]
    log.info(f"=== Début extraction (run_id: {run_id}) ===")

    validate_env()

    conn = sf_connect()
    log.info("Connecté à Snowflake")
    init_log_table(conn)

    last_activity = get_last_activity_date(conn)
    log.info(f"Dernière activité en base: {last_activity or 'aucune'}")

    workspace_ids = []
    scanner_result = {}
    results = []

    # =====================================================================
    # EXTRACTIONS + CHARGEMENTS
    # =====================================================================

    # 1. Workspaces (requis pour les étapes suivantes)
    def extract_ws():
        nonlocal workspace_ids
        df = extract_workspaces.extract()
        workspace_ids = df["id"].tolist() if "id" in df.columns else []
        return df

    results.append(run_step(
        conn, run_id, "RAW_WORKSPACES",
        extract_fn=extract_ws,
        load_fn=lambda df: load_snapshot(conn, df, "RAW_WORKSPACES"),
    ))

    # 2. Scanner API (6 tables)
    def extract_scan():
        nonlocal scanner_result
        scanner_result = extract_scanner.extract(workspace_ids)
        return scanner_result

    def load_scan(scanner):
        total = 0
        for key, table in [
            ("workspace_users", "RAW_WORKSPACE_USERS"),
            ("reports", "RAW_REPORTS"),
            ("report_users", "RAW_REPORT_USERS"),
            ("datasets", "RAW_DATASETS"),
            ("dashboards", "RAW_DASHBOARDS"),
            ("dataflows", "RAW_DATAFLOWS"),
        ]:
            total += load_snapshot(conn, scanner.get(key, pd.DataFrame()), table)
        return total

    results.append(run_step(
        conn, run_id, "RAW_SCANNER (6 tables)",
        extract_fn=extract_scan,
        load_fn=load_scan,
    ))

    # 3. Dataset refreshes
    results.append(run_step(
        conn, run_id, "RAW_DATASET_REFRESHES",
        extract_fn=extract_dataset_refreshes.extract,
        load_fn=lambda df: load_snapshot(conn, df, "RAW_DATASET_REFRESHES"),
    ))

    # 4. Capacités
    results.append(run_step(
        conn, run_id, "RAW_CAPACITIES",
        extract_fn=extract_capacities.extract,
        load_fn=lambda df: load_snapshot(conn, df, "RAW_CAPACITIES"),
    ))

    # 5. Apps + App users
    def extract_apps_all():
        df_apps, df_app_users = extract_apps.extract()
        return (df_apps, df_app_users)

    def load_apps_all(result):
        df_apps, df_app_users = result
        total = load_snapshot(conn, df_apps, "RAW_APPS")
        total += load_snapshot(conn, df_app_users, "RAW_APP_USERS")
        return total

    results.append(run_step(
        conn, run_id, "RAW_APPS + RAW_APP_USERS",
        extract_fn=extract_apps_all,
        load_fn=load_apps_all,
    ))

    # 6. Activité (UPSERT sur Id, chargement jour par jour)
    results.append(run_step(
        conn, run_id, "RAW_ACTIVITY",
        extract_fn=lambda: extract_activity.extract(
            last_activity, conn=conn,
            load_fn=lambda df: load_upsert(conn, df, "RAW_ACTIVITY", "Id"),
        ),
        load_fn=lambda df: 0,  # déjà chargé dans extract_fn
    ))

    # 7. Graph users
    results.append(run_step(
        conn, run_id, "RAW_GRAPH_USERS",
        extract_fn=extract_graph_users.extract,
        load_fn=lambda df: load_snapshot(conn, df, "RAW_GRAPH_USERS"),
    ))

    # 8. Graph licenses
    results.append(run_step(
        conn, run_id, "RAW_GRAPH_LICENSES",
        extract_fn=extract_graph_licenses.extract,
        load_fn=lambda df: load_snapshot(conn, df, "RAW_GRAPH_LICENSES"),
    ))

    # 9. User licenses
    results.append(run_step(
        conn, run_id, "RAW_USER_LICENSES",
        extract_fn=extract_user_licenses.extract,
        load_fn=lambda df: load_snapshot(conn, df, "RAW_USER_LICENSES"),
    ))

    # =====================================================================
    # RÉSUMÉ CONSOLE
    # =====================================================================
    conn.close()
    total_duration = round(time.time() - total_start, 1)

    log.info("")
    log.info(f"=== Résumé (run_id: {run_id}) — {total_duration}s ===")
    log.info(f"{'Table':<35} {'Statut':<10} {'Lignes':>8} {'Durée':>8}")
    log.info("-" * 65)

    errors = 0
    for r in results:
        if r["status"] == "ERREUR":
            errors += 1
        log.info(f"{r['table']:<35} {r['status']:<10} {r['rows']:>8} {r['duration']:>7.1f}s")

    log.info("-" * 65)

    # ── Notification Teams ────────────────────────────────────────────────
    _envoyer_teams(results, total_duration, run_id)

    if errors:
        log.warning(f"{errors} extraction(s) en erreur — voir RAW_EXTRACT_LOG")
        sys.exit(1)
    else:
        log.info("Toutes les extractions OK")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception(f"Erreur fatale: {e}")
        sys.exit(1)