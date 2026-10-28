# Stats Utilisateurs — Extraction Power BI & Graph → Snowflake

Pipeline d'extraction des données de gouvernance Power BI (API Admin + Scanner + Microsoft Graph) vers Snowflake.

## Architecture

```
                  ┌─────────────────┐     ┌─────────────────┐
                  │  Power BI Admin  │     │ Microsoft Graph  │
                  │       API        │     │       API        │
                  └────────┬─────┬──┘     └──────┬──────────┘
                           │     │               │
              Scanner API ─┘     │               │
                  (bulk)         │               │
                           ┌─────────────┐       │
                           │   main.py   │◄──────┘
                           │ orchestrateur│
                           └──────┬──────┘
                                  │
                           ┌──────▼──────┐
                           │  Snowflake  │
                           │  (schéma RAW)│
                           └─────────────┘
```

## Tables extraites

| Table Snowflake | Source | Mode | Description |
|---|---|---|---|
| `RAW_WORKSPACES` | PBI Admin | Full refresh | Workspaces actifs du tenant |
| `RAW_WORKSPACE_USERS` | PBI Scanner | Full refresh | Droits utilisateurs par workspace |
| `RAW_REPORTS` | PBI Scanner | Full refresh | Rapports par workspace |
| `RAW_REPORT_USERS` | PBI Scanner | Full refresh | Droits utilisateurs par rapport |
| `RAW_DATASETS` | PBI Scanner | Full refresh | Semantic models par workspace |
| `RAW_DASHBOARDS` | PBI Scanner | Full refresh | Dashboards par workspace |
| `RAW_DATAFLOWS` | PBI Scanner | Full refresh | Dataflows par workspace |
| `RAW_DATASET_REFRESHES` | PBI (non-admin) | Full refresh | Historique des 10 derniers refreshes par dataset* |
| `RAW_CAPACITIES` | PBI Admin | Full refresh | Capacités Premium / Fabric |
| `RAW_APPS` | PBI Admin | Full refresh | Applications publiées |
| `RAW_APP_USERS` | PBI Admin | Full refresh | Droits utilisateurs par app |
| `RAW_ACTIVITY` | PBI Admin | Upsert (clé `Id`) | Events d'activité (backfill 28j glissants) |
| `RAW_GRAPH_USERS` | Graph | Full refresh | Utilisateurs du tenant (+ department, company, jobTitle) |
| `RAW_GRAPH_LICENSES` | Graph | Full refresh | SKUs de licences du tenant |
| `RAW_USER_LICENSES` | Graph | Full refresh | Assignation licence → utilisateur |
| `RAW_EXTRACT_LOG` | — | Append | Logs d'exécution (statut, durée, erreurs) |

*\* `RAW_DATASET_REFRESHES` ne couvre que les workspaces où le Service Principal est membre (endpoint non-admin).*

## Fichiers

```
06_Stats utilisateurs/
├── main.py                       # Orchestrateur (point d'entrée)
├── config.py                     # Auth MSAL, helpers API
├── loader.py                     # Connexion Snowflake, load, logs
│
├── extract_workspaces.py         # GET admin/groups
├── extract_scanner.py            # POST admin/workspaces/getInfo (6 tables)
├── extract_dataset_refreshes.py  # GET groups/{id}/datasets/{id}/refreshes
├── extract_capacities.py         # GET admin/capacities
├── extract_apps.py               # GET admin/apps + admin/apps/{id}/users
├── extract_activity.py           # GET admin/activityevents (backfill J-28 → J-1)
├── extract_graph_users.py        # GET /users (Graph)
├── extract_graph_licenses.py     # GET /subscribedSkus (Graph)
├── extract_user_licenses.py      # GET /users?$select=assignedLicenses (Graph)
│
├── .env                          # Secrets (non versionné)
├── .env.example                  # Template des variables requises
├── requirements.txt              # Dépendances Python
│
├── Tests/                        # Scripts de test unitaire + CSV
└── Old/                          # Anciens fichiers
```

## Installation

```bash
# 1. Cloner / copier le dossier sur la VM

# 2. Installer les dépendances
pip install -r requirements.txt

# 3. Créer le fichier .env à partir du template
cp .env.example .env
# Remplir les valeurs dans .env
```

## Configuration (.env)

```env
# Service Principal (Entra ID)
TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
CLIENT_SECRET=votre_secret

# Snowflake
SNOWFLAKE_ACCOUNT=votre_compte.region
SNOWFLAKE_USER=votre_user
SNOWFLAKE_PASSWORD=votre_password
SNOWFLAKE_DATABASE=votre_database
SNOWFLAKE_SCHEMA=RAW
SNOWFLAKE_WAREHOUSE=votre_warehouse
SNOWFLAKE_ROLE=votre_role
```

## Exécution

```bash
# Lancer l'extraction complète
python main.py
```

Le script affiche un résumé en fin d'exécution :

```
=== Résumé (run_id: a3f7b2c1) — 45.2s ===
Table                               Statut       Lignes    Durée
-----------------------------------------------------------------
RAW_WORKSPACES                      OK              240     1.2s
RAW_SCANNER (6 tables)              OK             1376    12.4s
RAW_DATASET_REFRESHES               OK              160     8.3s
...
-----------------------------------------------------------------
Toutes les extractions OK
```

Le code de sortie est `0` si tout est OK, `1` si au moins une extraction a échoué.

## Cron (VM client)

```bash
# Exécution quotidienne à 6h
0 6 * * * cd /chemin/vers/06_Stats_utilisateurs && /usr/bin/python3 main.py >> /var/log/extract_pbi.log 2>&1
```

## Prérequis côté tenant

### Entra ID — App Registration
- **Aucune** permission Power BI (Tenant.Read.All, etc.) dans les API permissions
- Permissions Graph : `User.Read.All`, `Directory.Read.All` (type Application, avec consentement admin)

### Fabric Admin Portal — Tenant Settings
1. **"Les principaux services peuvent appeler les API publiques de Fabric"** → Activé
2. **"Les principaux de service peuvent accéder aux API d'administration en lecture seule"** → Activé, avec le groupe de sécurité contenant le SP

### Workspaces (pour les refreshes)
Le SP doit être ajouté comme **membre** des workspaces pour lesquels on veut récupérer l'historique de refresh (endpoint non-admin uniquement).

## Gestion des erreurs

- Chaque extraction est isolée : si une échoue, les autres continuent
- Les erreurs sont tracées dans `RAW_EXTRACT_LOG` (consultable dans Snowflake)
- L'activité utilise un **upsert** (MERGE sur `Id`) : pas de doublons si le script est relancé
- Le backfill d'activité est automatique : il récupère les jours manquants entre la dernière date en base et J-1 (max 28 jours)

## Requête utile — Vérifier les logs

```sql
-- Dernières exécutions
SELECT * FROM RAW_EXTRACT_LOG
ORDER BY STARTED_AT DESC
LIMIT 50;

-- Extractions en erreur
SELECT * FROM RAW_EXTRACT_LOG
WHERE STATUS = 'ERREUR'
ORDER BY STARTED_AT DESC;
```
