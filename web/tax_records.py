"""
    callable by cronjob, 0 0 2 * * : python console/check_taxes.py
"""

import logging
import os, sys, time
import json
import mysql.connector
import requests
from datetime import datetime, timedelta
from tax_mails import prepare_tax_mail, post_tax_mail
from typing import Union

def __read_json_file(filename, mode='r', encoding="utf-8") -> dict:
    with open(file=filename, mode=mode, encoding=encoding) as file:
        return json.load(file)
config = __read_json_file("../config/tax_check_config.json")

JOURNAL_DATEFORMAT = "%Y-%m-%d %H:%M:%S"

env_vars = {
    'DB_HOST' : os.getenv('DB_HOST'),
    'DB_PORT' : os.getenv('DB_PORT', '3306'),
    'DB_USER' : os.getenv('DB_USER'),
    'DB_PASSWORD' : os.getenv('DB_PASSWORD'),
    'DB_DATABASE' : os.getenv('DB_DATABASE'),

    'NEUCORE_BASE_URL' : os.getenv('API_BASE_URL'),
    # assuming mails eve-login was added to finance app, use key of finance
    # finance app has access to tokens of finance_mails eve-login
    'FINANCE_NEUCORE_KEY' : os.getenv('API_KEY'),
    'FINANCE_MAILS_EVE_LOGIN' : os.getenv('FINANCE_MAILS_EVE_LOGIN'),
    'FINANCE_MAILS_CHAR_NAME' : os.getenv('FINANCE_MAILS_CHAR_NAME')
}

def sql_list_format(values: list) -> str:
    """Formats a list of values into a string suitable for use in an SQL IN clause."""
    if len(values) == 0:
        return ""
    return "(" + ", ".join([f"'{x}'" for x in values]) + ")"


class TaxRecords:

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        logging.basicConfig(
            filename=f'{__name__}.log',
            encoding='utf-8',
            level=os.getenv('UWSGI_LOG_LEVEL', 'ERROR').upper()
        )
        self.__check_env_vars()

        finance_mails_char_id = self.__check_mailer_token()
        # set neucore evemail endpoint for the specified mailer character
        self.evemail_endpoint = (
            f"{env_vars['NEUCORE_V2_BASE_URL']}/characters/{finance_mails_char_id}/"
            f"mail/?datasource={finance_mails_char_id}:{env_vars['FINANCE_MAILS_EVE_LOGIN']}"
        )

        self.__db = mysql.connector.connect(
            host=env_vars['DB_HOST'],
            port=env_vars['DB_PORT'],
            user=env_vars['DB_USER'],
            password=env_vars['DB_PASSWORD'],
            database=env_vars['DB_DATABASE'],
        )

    def __check_env_vars(self):
        for key in env_vars.values():
            if key is None:
                self.logger.error('system environment variable %s not configured', key)
        env_vars['NEUCORE_V1_BASE_URL'] = env_vars['NEUCORE_BASE_URL'] + '/api/app/v1/esi'
        env_vars['NEUCORE_V2_BASE_URL'] = env_vars['NEUCORE_BASE_URL'] + '/api/app/v2/esi'
    def __check_mailer_token(self):
        """
        basic check that we can access neucore tokens of FINANCE_MAILS_EVE_LOGIN
        and have a matching token for FINANCE_MAILS_CHAR_NAME.
        """
        finance_mails_tokens_url = (
            f'{env_vars["NEUCORE_V1_BASE_URL"]}/eve-login/'
            f'{env_vars["FINANCE_MAILS_EVE_LOGIN"]}/token-data'
        )
        response = requests.get(
            finance_mails_tokens_url,
            headers={'Authorization': 'Bearer ' + env_vars['FINANCE_NEUCORE_KEY']},
            timeout=15
        )
        if response.status_code == 200:
            finance_mails_tokens = response.json()
        else:
            self.logger.info((
                    "check_taxes: %s error fetching tokens for neucore eve login: "
                    "{env_vars['FINANCE_MAILS_EVE_LOGIN']}, can not send evemails."
                ), response.status_code
            )
            sys.exit()
        if len(finance_mails_tokens) == 0:
            self.logger.info((
                    "check_taxes: no tokens added for neucore eve login: "
                    "%s, can not send evemails."
                ), env_vars['FINANCE_MAILS_EVE_LOGIN']
            )
            sys.exit()
        char_id = None
        for token in finance_mails_tokens:
            if token["characterName"] == env_vars["FINANCE_MAILS_CHAR_NAME"]:
                char_id = token["characterId"]
        if char_id is None:
            self.logger.info((
                    "check_taxes: no token added to neucore eve login: %s "
                    "matching FINANCE_MAILS_CHAR_NAME: %s, can not send evemails."
                ),
                env_vars['FINANCE_MAILS_EVE_LOGIN'],
                env_vars['FINANCE_MAILS_CHAR_NAME']
            )
            sys.exit()
        return char_id

    def select_corporation_wallet_journal(
        self,
        corporation_id: int,
        description_contains: str = "",
        ref_types: Union[list[str], None] = None,
        divisions: Union[list[int], None] = None,
        date_min: Union[datetime, None] = None,
        date_max: Union[datetime, None] = None
    ) -> list[dict]:
        """select from db with filters"""
        if ref_types is None:
            ref_types = []
        if divisions is None:
            divisions = []

        db_cursor = self.__db.cursor(dictionary=True)
        filters = []
        if corporation_id is not None:
            filters.append(f"corporation_id = {corporation_id}")
        if divisions is not None and len(divisions) > 0:
            filters.append(f"division IN {sql_list_format(divisions)}")
        if ref_types is not None and len(ref_types) > 0:
            filters.append(f"ref_type IN {sql_list_format(ref_types)}")
        if description_contains:
            filters.append(f"description LIKE '%{description_contains}%'")
        if date_min is not None:
            filters.append(f"journal_date >= '{date_min.strftime(JOURNAL_DATEFORMAT)}'")
        if date_max is not None:
            filters.append(f"journal_date < '{date_max.strftime(JOURNAL_DATEFORMAT)}'")
        row_filter = " AND ".join(filters)
        if row_filter != "":
            row_filter = f"WHERE {row_filter}"
        db_cursor.execute(
            f'''
                SELECT *
                FROM {config["wallet_journal_table_name"]}
                {row_filter}
            '''
        )
        res = db_cursor.fetchall()
        db_cursor.close()
        if res is None:
            return []
        return res

    def select_taxable_corps(self) -> list[dict]:
        """select taxed, active corps"""
        db_cursor = self.__db.cursor(dictionary=True)
        db_cursor.execute(
            f'''
                SELECT id, corporation_name, character_id, is_alt_corp, corporation_ceo_id, corporation_owner_id
                FROM {config["corp_info_table_name"]}
                WHERE active = 1 and is_taxed = 1;
            '''
        )
        res = db_cursor.fetchall()
        db_cursor.close()
        return res

    def insert_tax_record(self, tax_record: dict):
        """Insert tax record into db"""
        tax_record_columns = [
            "corporation_id", 
            "tax_month_date", 
            "taxable_income", 
            "corp_tax_amount", 
            "brave_tax_amount", 
            "brave_tax_payments"
        ]
        update_str = ", ".join(
            [
                f"{col_name} = VALUES({col_name})"
                for col_name in tax_record_columns[1:]
            ]
        )
        db_cursor = self.__db.cursor()
        db_cursor.execute(
            f'''
                INSERT INTO tax_records ({", ".join(tax_record_columns)})
                VALUES (
                    {tax_record["corporation_id"]},
                    '{tax_record["tax_month_date"].strftime(JOURNAL_DATEFORMAT)}',
                    {tax_record["taxable_income"]},
                    {tax_record["corp_tax_amount"]},
                    {tax_record["brave_tax_amount"]},
                    {tax_record["brave_tax_payments"]}
                )
                ON DUPLICATE KEY UPDATE {update_str};
            '''
        )
        self.__db.commit()
        db_cursor.close()

    def get_brave_tax_balance(
        self,
        corporation_id: int,
        tax_month: Union[datetime, None] = None
        # up to specific date
    ) -> int | None:
        """get brave tax balance for a corporation from db"""
        if tax_month is not None:
            max_date = tax_month.strftime(JOURNAL_DATEFORMAT)
            sql_filter = f"AND tax_month_date <= '{max_date}'"
        else:
            sql_filter = ""
        db_cursor = self.__db.cursor(dictionary=True)
        db_cursor.execute(
            f'''
                SELECT
                    brave_tax_amount, 
                    brave_tax_payments
                FROM tax_records 
                WHERE corporation_id = {corporation_id}
                {sql_filter};
            '''
        )
        t_records = db_cursor.fetchall()
        db_cursor.close()

        total_tax = sum(r["brave_tax_amount"] for r in t_records)
        total_payment = sum(r["brave_tax_payments"] for r in t_records)
        return total_payment - total_tax

    def update_tax_record(
        self,
        corporation: dict,
        year: int,
        month: int
    ):
        """make tax record, insert into database"""
        tax_month_start = datetime(year, month,1)
        if month < 12:
            tax_month_end = datetime(year, month + 1, 1)
        else:
            tax_month_end =datetime(year + 1, 1, 1)

        corporation_id = corporation["id"]
        corporation_name = corporation["corporation_name"]
        is_alt_corp = corporation["is_alt_corp"]
        corporation_ceo_id = corporation["corporation_ceo_id"]
        corporation_owner_id = corporation["corporation_owner_id"]

        tax_entries = self.select_corporation_wallet_journal(
            corporation_id,
            divisions=[1],
            ref_types=config["taxed_ref_types"],
            date_min=tax_month_start,
            date_max=tax_month_end
        )
        taxable_income = sum(entry['amount'] for entry in tax_entries)

        if is_alt_corp:
            base_tax = config['alt_corps_base_tax']
            tax_receiving_corp = config['alt_corps_tax_receiving_corp']
            tax_exempt_income = config['alt_corps_exempt_income']
        else:
            base_tax = config['main_corps_base_tax']
            tax_receiving_corp = config['main_corps_tax_receiving_corp']
            tax_exempt_income = config['main_corps_exempt_income']

        payment_entries = self.select_corporation_wallet_journal(
            corporation_id,
            ref_types=["corporation_account_withdrawal"],
            description_contains=tax_receiving_corp,
            date_min=tax_month_start,
            date_max=tax_month_end
        )

        brave_tax_payments = sum(entry['amount'] for entry in payment_entries) * -1
        corp_tax_amount = int(taxable_income/2)
        brave_tax_amount = max(0, int(taxable_income/2) - tax_exempt_income) + base_tax

        tax_record = {
            "corporation_id": corporation_id,
            "tax_month_date": tax_month_start,
            "taxable_income": taxable_income,
            "corp_tax_amount": corp_tax_amount,
            "brave_tax_amount": brave_tax_amount,
            "brave_tax_payments": brave_tax_payments,
            "corporation_name": corporation_name,
            "corporation_ceo_id": corporation_ceo_id,
            "corporation_owner_id": corporation_owner_id,
            "is_alt_corp": is_alt_corp,
        }
        self.insert_tax_record(tax_record)
        return tax_record

    def update_tax_records(
        self,
        year: int,
        month: int
    ):
        """update database tax records for all corps"""
        corporations = self.select_taxable_corps()
        tax_month_records = []
        if len(corporations) == 0:
            self.logger.info('check_taxes: no taxable corporations found in db, exiting')
        for corporation in corporations:
            month_record = self.update_tax_record(corporation, year, month)
            if month_record:
                tax_month_records.append(month_record)
        return tax_month_records

    def send_tax_evemails(self, balance_threshold: Union[int, None] = None):
        """Send tax reminders to every corp under the threshold."""
        # set up some dates for last month
        current_date = datetime.now()
        tax_month_end = datetime(current_date.year, current_date.month, 1)
        tax_month_last_day = tax_month_end - timedelta(days=1)

        self.logger.info(
            (
                "checking taxes, current date: %s, "
                "checking tax for month: %s-%s, "
                "threshold for evemails: %s"
             ),
            current_date,
            tax_month_last_day.year,
            tax_month_last_day.month,
            balance_threshold
        )

        month_records = self.update_tax_records(
            tax_month_last_day.year,
            tax_month_last_day.month
        )
        for record in month_records:
            record["brave_tax_balance"] = self.get_brave_tax_balance(
                record["corporation_id"],
                tax_month_last_day
            )
            if balance_threshold is not None and record["brave_tax_balance"] > balance_threshold:
                continue
            body, recipients, mail_subject = prepare_tax_mail(record, config)
            if record["corporation_name"] != "Second Sons":
                continue
            post_tax_mail(
                env_vars["FINANCE_NEUCORE_KEY"],
                self.evemail_endpoint,
                recipients,
                body,
                mail_subject,
            )

if __name__ == "__main__":
    tax_records = TaxRecords()
    tax_records.send_tax_evemails()
