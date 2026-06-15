"""
    callable by cronjob, 0 0 2 * * : python console/check_taxes.py
"""

import logging
import os
import sys
import time
import json
from typing import Union
from datetime import datetime, timedelta
import mysql.connector
import requests
from tax_mails import prepare_tax_mail, post_tax_mail
from slack_messages import SlackMessages

def read_json_file(filename, mode='r', encoding="utf-8") -> dict:
    with open(file=filename, mode=mode, encoding=encoding) as file:
        return json.load(file)
def write_json_file(
    contents,
    filename,
    create_dirs=True,
    mode='w',
    encoding="utf-8"
):
    if mode == 'wb':
        encoding=None
    # ensure directory for file exists if not creating in current directory
    dirname = os.path.dirname(filename)
    if (create_dirs and dirname != ''):
        os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(file=filename, mode=mode, encoding=encoding) as file:
        json.dump(contents, file, indent=4)

CONFIG_FILENAME = "../config/tax_check_config.json"
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
    'FINANCE_MAILS_CHAR_NAME' : os.getenv('FINANCE_MAILS_CHAR_NAME'),

    'FINANCE_SLACK_USER_OAUTH_TOKEN': os.getenv('FINANCE_SLACK_USER_OAUTH_TOKEN')
}

def sql_list_format(values: list) -> str:
    """Formats a list of values into a string suitable for use in an SQL IN clause."""
    if len(values) == 0:
        return ""
    return "(" + ", ".join([f"'{x}'" for x in values]) + ")"


class TaxRecords:

    def __init__(self, config: dict, logger = None):
        self.__config = config
        if logger is None:
            logger = logging.getLogger('tax_records')
            logging.basicConfig(
                filename='tax_records.log',
                encoding='utf-8',
                level=os.getenv('UWSGI_LOG_LEVEL', 'ERROR').upper()
            )
        self.logger = logger
        self.__check_env_vars()


        # set up some dates for last month
        self.today = datetime.now()
        self.tax_month_end = datetime(self.today.year, self.today.month, 1)
        self.tax_month_last_day = self.tax_month_end - timedelta(days=1)
        self.tax_year = self.tax_month_last_day.year
        self.tax_month = self.tax_month_last_day.month

        self.__slack = None

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

    def __init_slack(self) -> SlackMessages:
        if self.__slack is None:
            if env_vars['FINANCE_SLACK_USER_OAUTH_TOKEN'] is not None:
                self.__slack = SlackMessages(self.logger)
            else:
                self.logger.error((
                    '__init_slack called but environment variable'
                    'FINANCE_SLACK_USER_OAUTH_TOKEN is missing.'
                ))
        return self.__slack

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
                FROM {self.__config["wallet_journal_table_name"]}
                {row_filter}
            '''
        )
        res = db_cursor.fetchall()
        db_cursor.close()
        if res is None:
            return []
        return res

    def select_taxable_corp(self, corporation_id: int) -> dict:
        """select specific taxed, active corp"""
        db_cursor = self.__db.cursor(dictionary=True)
        db_cursor.execute(
            f'''
                SELECT * FROM corporations
                WHERE active = 1 and is_taxed = 1 and id = {corporation_id};
            '''
        )
        res = db_cursor.fetchone()
        db_cursor.close()
        return res
    def select_taxable_corps(self) -> list[dict]:
        """select all taxed, active corps"""
        db_cursor = self.__db.cursor(dictionary=True)
        db_cursor.execute(
            '''
                SELECT * FROM corporations
                WHERE active = 1 and is_taxed = 1;
            '''
        )
        res = db_cursor.fetchall()
        db_cursor.close()
        return res

    def insert_tax_record(self, corporation: dict):
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
                    {corporation["id"]},
                    '{corporation["tax_month_date"].strftime(JOURNAL_DATEFORMAT)}',
                    {corporation["taxable_income"]},
                    {corporation["corp_tax_amount"]},
                    {corporation["brave_tax_amount"]},
                    {corporation["brave_tax_payments"]}
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

        ref_types = self.__config["taxed_ref_types"]
        enabled_ref_types = [x for x in ref_types if ref_types[x]]

        tax_entries = self.select_corporation_wallet_journal(
            corporation["id"],
            divisions=[1],
            ref_types=enabled_ref_types,
            date_min=tax_month_start,
            date_max=tax_month_end
        )
        taxable_income = sum(entry['amount'] for entry in tax_entries)

        if corporation["is_alt_corp"]:
            base_tax = self.__config['alt_corps_base_tax']
            tax_receiving_corp = self.__config['alt_corps_tax_receiving_corp']
            tax_exempt_income = self.__config['alt_corps_exempt_income']
        else:
            base_tax = self.__config['main_corps_base_tax']
            tax_receiving_corp = self.__config['main_corps_tax_receiving_corp']
            tax_exempt_income = self.__config['main_corps_exempt_income']

        payment_entries = self.select_corporation_wallet_journal(
            corporation["id"],
            ref_types=["corporation_account_withdrawal"],
            description_contains=tax_receiving_corp,
            date_min=tax_month_start,
            date_max=tax_month_end
        )

        brave_tax_payments = sum(entry['amount'] for entry in payment_entries) * -1
        corp_tax_amount = int(taxable_income/2)
        brave_tax_amount = max(0, int(taxable_income/2) - tax_exempt_income) + base_tax

        corporation["tax_month_date"] = tax_month_start
        corporation["taxable_income"] = taxable_income
        corporation["corp_tax_amount"] = corp_tax_amount
        corporation["brave_tax_amount"] = brave_tax_amount
        corporation["brave_tax_payments"] = brave_tax_payments

        self.insert_tax_record(corporation)
        return corporation

    def update_tax_records(
        self,
        year: int,
        month: int
    ):
        """update database tax records for all corps"""
        corporations = self.select_taxable_corps()
        if len(corporations) == 0:
            self.logger.info(
                'update_tax_records: no taxable corporations found in db'
            )
        for corp in corporations:
            self.update_tax_record(corp, year, month)
        return corporations

    def send_tax_evemail(self, corporation_id: int):
        """Send tax evemail to a specific corp."""
        self.logger.info((
                "checking taxes, current date: %s, "
                "checking tax for month: %s-%s, "
                "corporation checked: %s"
             ),
            self.today, self.tax_year, self.tax_month, corporation_id
        )
        corp = self.select_taxable_corp(corporation_id)
        self.update_tax_record(corp, self.tax_year, self.tax_month)
        corp["brave_tax_balance"] = self.get_brave_tax_balance(
            corp["id"],
            self.tax_month_last_day
        )
        body, recipients, mail_subject = prepare_tax_mail(corp, self.__config)
        post_tax_mail(
            env_vars["FINANCE_NEUCORE_KEY"],
            self.evemail_endpoint,
            recipients,
            body,
            mail_subject,
            logger = self.logger
        )

    def send_tax_evemails(
        self,
        balance_threshold: Union[int, None] = None,
        web_called = False
    ):
        """
            - Update tax records of taxable corps for the previous month.
            - Send tax evemails to every taxeable corp for that month.
            - Waits 13 seconds inbetween evemails to satisfy ccp rate limit.

            Respects the low balance threshold for evemails if provided.

            This can be used through the web app if you set the timeout long
            enough, but that's a bit for fun, the intention is a cronjob.
        """

        taxed_corporations = self.update_tax_records(
            self.tax_year,
            self.tax_month
        )
        for corp in taxed_corporations:
            corp["brave_tax_balance"] = self.get_brave_tax_balance(
                corp["id"],
                self.tax_month_last_day
            )
        if balance_threshold is not None:
            taxed_corporations = [
                corp for corp in taxed_corporations if
                corp["brave_tax_balance"] > balance_threshold
            ]

        self.logger.info(
            (
                "checking taxes, current date: %s, "
                "checking tax for month: %s-%s, "
                "threshold for evemails: %s, "
                "%s corporations to mail"
             ),
            self.today,
            self.tax_year,
            self.tax_month,
            balance_threshold,
            len(taxed_corporations)
        )
        if web_called:
            self.__config['total_mails'] = len(taxed_corporations)
            self.__config['mailing'] = True
            self.__config['mail_progress'] = 0
            self.__config['mail_start'] = self.today.strftime(JOURNAL_DATEFORMAT)
            write_json_file(self.__config, CONFIG_FILENAME)

        for corp in taxed_corporations:
            if web_called:
                self.__config = read_json_file(CONFIG_FILENAME)
                if self.__config.get('mailing', True) is False:
                    self.logger.error('mailing task cancelled by web client.')
                    self.__config['mail_cancelled'] = datetime.now().strftime(JOURNAL_DATEFORMAT)
                    write_json_file(self.__config, CONFIG_FILENAME)
                    return taxed_corporations
            body, recipients, mail_subject = prepare_tax_mail(corp, self.__config)
            if web_called:
                self.__config['mail_progress'] += 1
                write_json_file(self.__config, CONFIG_FILENAME)
            # internal rate lim: 5/min, ~13s sleep
            time.sleep(13)
            post_tax_mail(
                env_vars["FINANCE_NEUCORE_KEY"],
                self.evemail_endpoint,
                recipients,
                body,
                mail_subject,
                logger = self.logger
            )
        if web_called:
            self.__config['total_mails'] = 0
            write_json_file(self.__config, CONFIG_FILENAME)
        return taxed_corporations

    def send_slack_messages(self, corporations: list[dict]):
        '''
            send a tax record slack message to each corp owner.

            corporation {
                corporation_name: str
                owner_name: str
                tax_record: dict
            }
        '''
        slack = self.__init_slack()
        for corp in corporations:
            owner_name = corp.get('owner_name')
            if owner_name is None or owner_name == '':
                continue
            owner_slack = slack.get_member_by_real_name(owner_name)
            if owner_slack is None:
                self.logger.info(
                    'owner %s of %s not found on slack',
                    owner_name, corp['corporation_name']
                )
                continue
            message = slack.format_slack_report(
                corp,
                self.__config
            )
            slack.send_message(owner_slack['id'], message)
    def send_slack_tax_notif(self, corporations):
        '''
            notify corp owner channels that tax reports have been sent.
        '''
        slack = self.__init_slack()
        n_corps = len(corporations)
        m_date = self.tax_month_last_day
        message = (
            f":moneybag: {m_date.strftime("%B")} {m_date.strftime("%Y")}"
            f"Tax Reports have been sent, {n_corps} taxed corporations."
        )
        alt_tax_slack_c = slack.get_channel_by_name(
            self.__config['alt_corps_tax_help_channel']
        )
        main_tax_slack_c = slack.get_channel_by_name(
            self.__config['main_corps_tax_help_channel']
        )
        if alt_tax_slack_c is not None:
            slack.send_message(
                alt_tax_slack_c['id'],
                message
            )
        if main_tax_slack_c is not None:
            if (
                alt_tax_slack_c is not None and
                main_tax_slack_c['id'] == alt_tax_slack_c['id']
            ):
                # same channel > already messaged
                return
            slack.send_message(
                main_tax_slack_c['id'],
                message
            )

if __name__ == "__main__":
    tax_config = read_json_file(CONFIG_FILENAME)
    tax_records = TaxRecords(tax_config)
    taxed_corps = tax_records.send_tax_evemails()
    tax_records.send_slack_messages(taxed_corps)
    tax_records.send_slack_tax_notif(taxed_corps)
