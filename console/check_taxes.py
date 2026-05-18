# called by cronjob, 002** : python console/check_taxes.py

import os
import mysql.connector
import requests
import json
from datetime import datetime, timedelta
from common import BRAVE_DATEFORMAT
from tax_mails import prepare_tax_mail, post_tax_mail
import common

# get some dates for last month
current_date = datetime.now()
tax_month_end = datetime(current_date.year, current_date.month, 1)
tax_month_last_day = tax_month_end - timedelta(days=1)
tax_month_start = datetime(tax_month_last_day.year, tax_month_last_day.month,1)


env_vars = {
    'db_host' : os.getenv('DB_HOST'),
    'db_port' : os.getenv('DB_PORT', 3306),
    'db_user' : os.getenv('DB_USER'),
    'db_password' : os.getenv('DB_PASSWORD'),
    'db_database' : os.getenv('DB_DATABASE'),

    'neucore_base_url' : os.getenv('API_BASE_URL'),
    'mailer_neucore_key' : os.getenv('MAILER_NEUCORE_KEY'),
    'mailer_neucore_login_name' : os.getenv('MAILER_NEUCORE_LOGIN_NAME'),
    'mailer_character_id' : os.getenv('MAILER_CHARACTER_ID'),
}

for key in env_vars:
    if env_vars[key] is None:
        print(f'check_taxes: system environment variable {key} not configured')
        exit()

try:
    env_vars['mailer_character_id'] = int(env_vars['mailer_character_id'])
except ValueError:
    print(f'check_taxes: mailer_character_id env var not an integer: {env_vars["mailer_character_id"]}')
    exit()
# is it similar to ccp esi token? -> could get character_id from token instead of needing it as an env var using validate_eve_jwt 

# I think we just assume the neucore key is valid?

env_vars['neucore_base_url'] += '/api/app/v2/esi/latest'
evemail_auth_header = {'Authorization': 'Bearer ' + env_vars['mailer_neucore_key']}
evemail_endpoint = f"{env_vars['neucore_base_url']}/characters/{env_vars['mailer_character_id']}/mail/?datasource={env_vars['mailer_character_id']}:{env_vars['mailer_neucore_login_name']}"

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

def select_active_corps() -> list[dict]:
    db_cursor.execute(
        f'''
            SELECT id, corporation_name, character_id, is_alt_corp, corporation_ceo_id, corporation_owner_id
            FROM {common.config["corp_info_table_name"]} WHERE active = 1
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
                        UPDATE {common.config["corp_info_table_name"]}
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
                        UPDATE {common.config["corp_info_table_name"]}
                        SET corporation_ceo_id = '{corporation_info["ceo_id"]}'
                        WHERE id = {corporation_id};
                    '''
                )
        # cant verify correct is_alt_corp, character_id, corporation_owner_id
    brave_db.commit()
    # refresh corp info from db after possible updates
    return select_active_corps()

corporations = select_active_corps()
corporations = check_corp_info(corporations)

print(f"check taxes: Current date: {current_date}, Tax month start: {tax_month_start}, Tax month end: {tax_month_end}")

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
                '{tax_record["tax_month_date"].strftime(BRAVE_DATEFORMAT)}',
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
    date_min: datetime = tax_month_start, 
    date_max: datetime = tax_month_end
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
            FROM {common.config["wallet_journal_table_name"]} 
            {filter}
        '''
    )
    res = db_cursor.fetchall()
    if res is None:
        return []
    return res

def check_corp_tax(corporation: dict):
    corporation_id = corporation["id"]
    corporation_name = corporation["corporation_name"]
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
    
    if corporation_name in common.config["excluded_corporations"]:
        print(f"{num_checked}/{num_corporations}", corporation_name, "- excluded")
    elif tax_month_start > prev_tax_month :
        tax_entries = select_corporation_wallet_journal(
            corporation_id,
            divisions=[1],
            ref_types=common.config["taxed_ref_types"],
            date_min=tax_month_start, 
            date_max=tax_month_end
        )
        taxable_income = sum([entry['amount'] for entry in tax_entries])

        if is_alt_corp:
            base_tax = common.config['alt_corps_base_tax']
            tax_receiving_corp = common.config['alt_corps_tax_receiving_corp']
            taxable_income = max(0, taxable_income - common.config['alt_corps_exempt_income'])
        else:
            base_tax = common.config['main_corps_base_tax']
            tax_receiving_corp = common.config['main_corps_tax_receiving_corp']
            taxable_income = max(0, taxable_income - common.config['main_corps_exempt_income'])

        payment_entries = select_corporation_wallet_journal(
            corporation_id,
            ref_types=["corporation_account_withdrawal"],
            description_contains=tax_receiving_corp,
            date_min=tax_month_start, 
            date_max=tax_month_end
        )

        brave_tax_payments = sum([entry['amount'] for entry in payment_entries]) * -1
        corp_tax_amount = int(taxable_income/2)
        brave_tax_amount = taxable_income - corp_tax_amount + base_tax
        brave_tax_balance = prev_balance - brave_tax_amount + brave_tax_payments

        tax_record = {
            "corporation_id": corporation_id,
            "tax_month_date": tax_month_start,
            "taxable_income": taxable_income,
            "corp_tax_amount": corp_tax_amount,
            "brave_tax_amount": brave_tax_amount,
            "brave_tax_payments": brave_tax_payments,
            "brave_tax_balance": brave_tax_balance,
            "corporation_name": corporation_name,
            "corporation_ceo_id": corporation_ceo_id,
            "corporation_owner_id": corporation_owner_id,
            "is_alt_corp": is_alt_corp,
        }

        print(f"{num_checked}/{num_corporations}",tax_record)
        insert_tax_record(tax_record)
        if (is_alt_corp and common.config["evemail_alt_corps"]) or (not is_alt_corp and common.config["evemail_main_corps"]):
            body, recipients, mail_subject = prepare_tax_mail(tax_record)
            post_tax_mail(
                env_vars["mailer_neucore_key"],
                evemail_endpoint,
                recipients,
                body,
                mail_subject,
            )
    else:
        if previous_record is not None:
            previous_record["corporation_name"] = corporation_name
            previous_record["corporation_ceo_id"] = corporation_ceo_id
            previous_record["corporation_owner_id"] = corporation_owner_id
            previous_record["is_alt_corp"] = is_alt_corp
        print(f"{num_checked}/{num_corporations}", previous_record, "- already taxed, old record")

    return

num_corporations = len(corporations)
num_checked = 1
for corporation in corporations:
    check_corp_tax(corporation)
    num_checked += 1

# do a nice close and commit at the end just in case
brave_db.commit()
db_cursor.close()
brave_db.close()