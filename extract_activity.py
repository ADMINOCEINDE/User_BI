"""Extraction des events d'activité -> RAW_ACTIVITY"""

from datetime import datetime, timedelta, timezone

import requests as _requests
import pandas as pd

from config import PBI_BASE, pbi_headers, api_get, ACTIVITY_MAX_DAYS, log


def _extract_day(date: str) -> list[dict]:
    """Extrait les events pour un jour (format YYYY-MM-DD)."""
    start = f"'{date}T00:00:00'"
    end = f"'{date}T23:59:59'"
    url = f"{PBI_BASE}/admin/activityevents?startDateTime={start}&endDateTime={end}"
    headers = pbi_headers()
    all_events = []

    while url:
        try:
            r = api_get(url, headers)
        except _requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 400:
                log.warning(f"  {date} hors fenêtre de rétention API, ignoré.")
                return []
            raise
        data = r.json()
        all_events.extend(data.get("activityEventEntities", []))
        token = data.get("continuationToken")
        url = f"{PBI_BASE}/admin/activityevents?continuationToken='{token}'" if token else None

    return all_events


def extract(last_date_in_sf: str | None, conn=None, load_fn=None) -> pd.DataFrame:
    """Backfill intelligent : récupère les jours manquants jusqu'à J-1.

    Si conn + load_fn fournis : charge chaque jour au fil de l'eau (recommandé).
    Sinon : retourne un DataFrame agrégé (comportement legacy).
    """
    log.info("Extraction activité...")
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    oldest = today - timedelta(days=ACTIVITY_MAX_DAYS)

    if last_date_in_sf:
        start = datetime.strptime(last_date_in_sf, "%Y-%m-%d").date() + timedelta(days=1)
        start = max(start, oldest)
    else:
        start = oldest

    if start > yesterday:
        log.info("  Activité déjà à jour.")
        return pd.DataFrame()

    total_rows = 0
    all_events = []
    current = start

    while current <= yesterday:
        day_str = current.strftime("%Y-%m-%d")
        log.info(f"  Activity {day_str}...")
        events = _extract_day(day_str)
        log.info(f"    {len(events)} events")

        if events:
            df_day = pd.json_normalize(events)
            if conn is not None and load_fn is not None:
                total_rows += load_fn(df_day)  # load immédiat
            else:
                all_events.extend(events)

        current += timedelta(days=1)

    if conn is not None and load_fn is not None:
        log.info(f"  Total chargé: {total_rows} events")
        return pd.DataFrame()  # déjà chargé jour par jour

    log.info(f"  Total: {len(all_events)} events")
    if not all_events:
        return pd.DataFrame()
    return pd.json_normalize(all_events)
