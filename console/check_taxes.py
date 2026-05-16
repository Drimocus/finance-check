# called by cronjob, 002** : python console/check_taxes.py

import os
import mysql.connector
import requests
from datetime import datetime, timedelta

# mock env
os.environ['API_BASE_URL'] = ''
os.environ['API_KEY'] = ''
os.environ['API_EVE_LOGIN'] = ''
os.environ['DB_HOST'] = ''
os.environ['DB_PORT'] = ''
os.environ['DB_USER'] = ''
os.environ['DB_PASSWORD'] = ''
os.environ['DB_DATABASE'] = ''

BRAVE_DATEFORMAT = "%Y-%m-%d %H:%M:%S"
TAXED_REF_TYPES = [
    "bounty_prizes", 
    "ess_escrow_transfer", 
    "project_discovery_reward", 
    "agent_mission_reward", 
    "agent_mission_time_bonus_reward", 
    "daily_goal_payouts", 
    "freelance_jobs_reward"
]

env_vars = {
    'api_base_url' : os.getenv('API_BASE_URL'),
    'api_key' : os.getenv('API_KEY'),
    # user info for brave api relay (datasource=char_id:api_login_name)
    'api_login_name' : os.getenv('API_EVE_LOGIN'),
    'db_host' : os.getenv('DB_HOST'),
    'db_port' : os.getenv('DB_PORT', 3306),
    'db_user' : os.getenv('DB_USER'),
    'db_password' : os.getenv('DB_PASSWORD'),
    'db_database' : os.getenv('DB_DATABASE'),
    # corporation_ids with all wallet entry ref_type's are tracked (dont need)
    # 'all_types_corporations' : os.getenv('ALL_TYPES_CORPORATIONS'),

    # WEB ONLY
    # 'eve_app_id': os.getenv('EVE_APP_ID'),
    # 'eve_app_secret': os.getenv('EVE_APP_SECRET'),
    # 'eve_app_callback': os.getenv('EVE_APP_CALLBACK'),
    # 'secret_key': os.getenv('SECRET_KEY'),
    # 'check_alliances': os.getenv('CHECK_ALLIANCES'),
    # 'check_corporations': os.getenv('CHECK_CORPORATIONS)'
}

for key in env_vars:
    if env_vars[key] is None:
        print(f'check_taxes: system environment variable {key} not configured')
        exit()

env_vars['api_base_url'] += '/api/app/v2/esi/latest'
api_auth_header = {'Authorization': 'Bearer ' + env_vars['api_key']}

try:
    brave_db = mysql.connector.connect(
        host=env_vars['db_host'],
        port=env_vars['db_port'],
        user=env_vars['db_user'],
        password=env_vars['db_password'],
        database=env_vars['db_database'],
    )
except mysql.connector.ProgrammingError as err:
    print(f'check_taxes: could not connect to database {err}')
    exit()
db_cursor = brave_db.cursor(dictionary=True)

def get_corp_info() -> list[dict]:
    db_cursor.execute(
        '''
            SELECT id, corporation_name, character_id, is_alt_corp, corporation_ceo_id, corporation_owner_id
            FROM corporations WHERE active = 1
        '''
    )
    return db_cursor.fetchall()

def check_corp_info(corporations: list[dict]):

    db_corp_ids = [corporation["id"] for corporation in corporations]

    names_endpoint = "https://esi.evetech.net/universe/names"
    names_res = requests.post(
        url = names_endpoint,
        data=json.dumps(db_corp_ids)
    )
    if names_res.status_code != 200:
        print(f'check_taxes: Failed to get ccp corp names - [code {names_res.status_code}]')
        ccp_corp_names = None
    else:
        ccp_corp_names = names_res.json()

    for corporation in corporations:
        corporation_id = corporation["id"]
        corporation_name = corporation["corporation_name"]
        character_id = corporation["character_id"]
        is_alt_corp = corporation["is_alt_corp"]
        corporation_ceo_id = corporation["corporation_ceo_id"]
        corporation_owner_id = corporation["corporation_owner_id"]

        # has bulk lookup so we can do a nice quick check
        if ccp_corp_names is not None:
            ccp_name = next((item for item in ccp_corp_names if item["id"] == corporation_id), None)
            if ccp_name is None:
                print(f'check_taxes: db has an invalid corp, id:{corporation_id} name:{corporation_name}, not found in ccp names endpoint')
            elif ccp_name["category"] != "corporation":
                print(f'check_taxes: db has an invalid corp, id:{corporation_id} name:{corporation_name}, is a {ccp_name["category"]} in ccp names endpoint')
            elif ccp_name["name"] != corporation_name:
                print(f'check_taxes: name mismatch between db and ccp, db: {corporation_name} ccp: {ccp_name["name"]}, using ccp name')
                db_cursor.execute(
                    f'''
                        UPDATE corporations
                        SET corporation_name = '{ccp_name["name"]}'
                        WHERE id = {corporation_id};
                    '''
                )

        # no bulk lookup, only check if missing
        if corporation_ceo_id is None or corporation_ceo_id == 0:
            corporation_info_res = requests.get(f"https://esi.evetech.net/corporations/{corporation_id}")

            if corporation_info_res.status_code != 200:
                print(f'check_taxes: Failed to get ccp corp info for id:{corporation_id}, name: {corporation_name} ceo: {corporation_ceo_id} - [code {corporation_info_res.status_code}]')
            else:
                corporation_info = corporation_info_res.json()
                db_cursor.execute(
                    f'''
                        UPDATE corporations
                        SET corporation_ceo_id = '{corporation_info["ceo_id"]}'
                        WHERE id = {corporation_id};
                    '''
                )
        # cant verify correct is_alt_corp, character_id, corporation_owner_id
    brave_db.commit()
    # refresh corp info from db after possible updates
    return get_corp_info()

corporations = get_corp_info()
corporations = check_corp_info(corporations)

# get some dates for last month
current_date = datetime.now()
prev_month_end = datetime(current_date.year, current_date.month, 1)
prev_month_last_day = prev_month_end - timedelta(days=1)
prev_month_start = datetime(prev_month_last_day.year, prev_month_last_day.month,1)

print(current_date, prev_month_start, prev_month_end)

def insert_tax_record(tax_record: dict):
    db_cursor.execute(
        f'''
            INSERT INTO tax_records (
                corporation_id, 
                tax_month_date, 
                taxable_income, 
                corp_tax_amount, 
                brave_tax_amount, 
                brave_tax_payments, 
                brave_tax_balance
            ) VALUES (
                {tax_record["corporation_id"]},
                '{tax_record["tax_month_date"]}',
                {tax_record["taxable_income"]},
                {tax_record["corp_tax_amount"]},
                {tax_record["brave_tax_amount"]},
                {tax_record["brave_tax_payments"]},
                {tax_record["brave_tax_balance"]}
            );
        '''
    )
    brave_db.commit()

def get_last_tax_record(corporation_id: int) -> dict | None:
    db_cursor.execute(
        f'''
            SELECT
                corporation_id, 
                tax_month_date, 
                taxable_income, 
                corp_tax_amount, 
                brave_tax_amount, 
                brave_tax_payments, 
                brave_tax_balance
            FROM tax_records 
            WHERE corporation_id = {corporation_id}
            AND tax_month_date = (SELECT MAX(tax_month_date) FROM tax_records WHERE corporation_id = {corporation_id});
        '''
    )
    return db_cursor.fetchone()

def sql_list_format(values: list) -> str:
    """Formats a list of values into a string suitable for use in an SQL IN clause."""
    if len(values) == 0: return ""
    return "(" + ", ".join([f"'{x}'" for x in values]) + ")"

def select_corporation_wallet_journal(
    corporation_id: int, 
    description_contains: str = "",
    ref_types: list[str] = [], 
    divisions: list[int] = [],
    date_min: datetime = prev_month_start, 
    date_max: datetime = prev_month_end
) -> list[dict]:
    filters = []
    if corporation_id is not None:
        filters.append(f"corporation_id = {corporation_id}")
    if divisions is not None and len(divisions) > 0:
        filters.append(f"division IN {sql_list_format(divisions)}")
    if ref_types is not None and len(ref_types) > 0:
        filters.append(f"ref_type IN {sql_list_format(ref_types)}")
    if description_contains:
        filters.append(f"description LIKE '%{description_contains}%'")
    filters.append(f"journal_date >= '{date_min.strftime(BRAVE_DATEFORMAT)}'")
    filters.append(f"journal_date < '{date_max.strftime(BRAVE_DATEFORMAT)}'")
    filter = " AND ".join(filters)
    if filter != "":
        filter = f"WHERE {filter}"
    db_cursor.execute(
        f'''
            SELECT *
            FROM wallet_journal 
            {filter}
        '''
    )
    res = db_cursor.fetchall()
    if res is None:
        return []
    return res

def check_corp_tax(corporation: tuple):
    corporation_id = corporation["id"]
    corporation_name = corporation["corporation_name"]
    character_id = corporation["character_id"]
    is_alt_corp = corporation["is_alt_corp"]
    corporation_ceo_id = corporation["corporation_ceo_id"]
    corporation_owner_id = corporation["corporation_owner_id"]

    previous_record = get_last_tax_record(corporation_id)
    if previous_record is None:
        prev_balance = 0
        prev_tax_month = datetime.fromtimestamp(0)
    else:
        prev_balance = previous_record["brave_tax_balance"]
        prev_tax_month = previous_record["tax_month_date"]
    
    print(prev_month_start, prev_tax_month)
    if prev_month_start > prev_tax_month:
        tax_entries = select_corporation_wallet_journal(
            corporation_id,
            divisions=[1],
            ref_types=TAXED_REF_TYPES,
            date_min=prev_month_start, 
            date_max=prev_month_end
        )

        payment_entries = select_corporation_wallet_journal(
            corporation_id,
            ref_types=["corporation_account_withdrawal"],
            description_contains="to Brave United Holding",
            date_min=prev_month_start, 
            date_max=prev_month_end
        )

        taxable_income = sum([entry['amount'] for entry in tax_entries])
        brave_tax_payments = sum([entry['amount'] for entry in payment_entries]) * -1
        corp_tax_amount = int(taxable_income/2)
        brave_tax_amount = taxable_income - corp_tax_amount + 100_000_000
        brave_tax_balance = prev_balance - brave_tax_amount + brave_tax_payments

        tax_record = {
            "corporation_id": corporation_id,
            "tax_month_date": prev_month_start.strftime(BRAVE_DATEFORMAT),
            "taxable_income": taxable_income,
            "corp_tax_amount": corp_tax_amount,
            "brave_tax_amount": brave_tax_amount,
            "brave_tax_payments": brave_tax_payments,
            "brave_tax_balance": brave_tax_balance
        }

        print(tax_record)
        insert_tax_record(tax_record)
    else:
        print(previous_record)

    return

for corporation in corporations:
    check_corp_tax(corporation)

brave_db.commit()
db_cursor.close()
brave_db.close()