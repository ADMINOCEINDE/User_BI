"""Fonctions de chargement Snowflake + logging des extractions."""

import os
import logging
from datetime import datetime, timezone

import pandas as pd
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas
from dotenv import load_dotenv
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.serialization import load_pem_private_key, Encoding, PrivateFormat, NoEncryption

load_dotenv()

log = logging.getLogger("extract")

REQUIRED_ENV_VARS = [
    "TENANT_ID", "CLIENT_ID", "CLIENT_SECRET",
    "SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PRIVATE_KEY_PATH",
    "SNOWFLAKE_DATABASE", "SNOWFLAKE_SCHEMA", "SNOWFLAKE_WAREHOUSE", "SNOWFLAKE_ROLE",
]


def validate_env():
    """Vérifie que toutes les variables d'environnement requises sont définies."""
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        raise EnvironmentError(f"Variables manquantes dans .env : {', '.join(missing)}")


def sf_connect() -> snowflake.connector.SnowflakeConnection:
    key_path = os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]
    passphrase = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE") or None
    with open(key_path, "rb") as f:
        private_key = load_pem_private_key(
            f.read(),
            password=passphrase.encode() if passphrase else None,
            backend=default_backend(),
        )
    private_key_bytes = private_key.private_bytes(
        encoding=Encoding.DER,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        private_key=private_key_bytes,
        database=os.environ["SNOWFLAKE_DATABASE"],
        schema=os.environ["SNOWFLAKE_SCHEMA"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        role=os.environ["SNOWFLAKE_ROLE"],
    )


def load_snapshot(conn, df: pd.DataFrame, table: str) -> int:
    """TRUNCATE + INSERT (full refresh). Retourne le nb de lignes."""
    if df.empty:
        log.info(f"  {table}: vide, skip")
        return 0
    df = df.copy()
    df.columns = [c.upper() for c in df.columns]
    df["_INGESTED_AT"] = datetime.now(timezone.utc).isoformat()
    write_pandas(conn, df, table, auto_create_table=True, overwrite=True)
    log.info(f"  {table}: {len(df)} lignes chargées (full refresh)")
    return len(df)


def load_upsert(conn, df: pd.DataFrame, table: str, key: str) -> int:
    """MERGE/UPSERT sur une clé. Évite les doublons si re-exécution."""
    if df.empty:
        log.info(f"  {table}: vide, skip")
        return 0
    df = df.copy()
    # Remplace les '.' par '_' (json_normalize génère des noms avec points)
    df.columns = [c.upper().replace(".", "_") for c in df.columns]
    key_upper = key.upper().replace(".", "_")
    df["_INGESTED_AT"] = datetime.now(timezone.utc).isoformat()

    staging = f"{table}_STAGING"
    cur = conn.cursor()

    # Charger dans staging d'abord (bon schéma garanti)
    write_pandas(conn, df, staging, auto_create_table=True, overwrite=True)

    # Créer la table cible si elle n'existe pas
    cur.execute(f'CREATE TABLE IF NOT EXISTS "{table}" LIKE "{staging}"')

    # Vérifier que la colonne clé existe dans la table cible (schema mismatch possible)
    try:
        cur.execute(f'SELECT "{key_upper}" FROM "{table}" LIMIT 0')
    except Exception:
        log.warning(f"  {table}: colonne clé '{key_upper}' absente, recréation de la table")
        cur.execute(f'DROP TABLE IF EXISTS "{table}"')
        cur.execute(f'CREATE TABLE "{table}" LIKE "{staging}"')

    # Ajouter les colonnes présentes dans staging mais absentes de la cible (schema evolution)
    cur.execute(f'DESCRIBE TABLE "{staging}"')
    staging_schema = {row[0].upper(): row[1] for row in cur.fetchall()}
    cur.execute(f'DESCRIBE TABLE "{table}"')
    target_cols = {row[0].upper() for row in cur.fetchall()}
    for col, col_type in staging_schema.items():
        if col not in target_cols:
            cur.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {col_type}')

    # MERGE : insert only (pas d'update, on garde la première version)
    cols = ", ".join([f'"{c}"' for c in df.columns])
    src_cols = ", ".join([f'src."{c}"' for c in df.columns])

    cur.execute(f"""
        MERGE INTO "{table}" tgt
        USING "{staging}" src
        ON tgt."{key_upper}" = src."{key_upper}"
        WHEN NOT MATCHED THEN
            INSERT ({cols}) VALUES ({src_cols})
    """)

    inserted = cur.rowcount
    cur.execute(f'DROP TABLE IF EXISTS "{staging}"')
    log.info(f"  {table}: {inserted} nouvelles lignes (upsert sur {key_upper})")
    return inserted


def init_log_table(conn):
    """Crée la table RAW_EXTRACT_LOG si elle n'existe pas."""
    conn.cursor().execute("""
        CREATE TABLE IF NOT EXISTS "RAW_EXTRACT_LOG" (
            "RUN_ID"        VARCHAR,
            "TABLE_NAME"    VARCHAR,
            "STATUS"        VARCHAR,
            "ROW_COUNT"     INTEGER,
            "DURATION_SEC"  FLOAT,
            "ERROR_MESSAGE" VARCHAR,
            "STARTED_AT"    TIMESTAMP_NTZ,
            "FINISHED_AT"   TIMESTAMP_NTZ
        )
    """)


def write_log(conn, run_id: str, table_name: str, status: str,
              row_count: int, duration_sec: float, error_message: str,
              started_at: str, finished_at: str):
    """Écrit une ligne de log dans RAW_EXTRACT_LOG."""
    conn.cursor().execute("""
        INSERT INTO "RAW_EXTRACT_LOG"
        ("RUN_ID","TABLE_NAME","STATUS","ROW_COUNT","DURATION_SEC","ERROR_MESSAGE","STARTED_AT","FINISHED_AT")
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (run_id, table_name, status, row_count, duration_sec,
          error_message, started_at, finished_at))


def get_last_activity_date(conn) -> str | None:
    cur = conn.cursor()
    try:
        cur.execute('SELECT MAX(TO_DATE("CREATIONTIME"))::VARCHAR FROM "RAW_ACTIVITY"')
        row = cur.fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None
