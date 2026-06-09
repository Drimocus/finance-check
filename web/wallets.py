"""
    called by cronjob, what schedule ?
"""

import datetime
import os, time
from typing import Optional

import mysql.connector
import requests
import logging

env_vars = {
    'DB_HOST' : os.getenv('DB_HOST'),
    'DB_PORT' : os.getenv('DB_PORT', '3306'),
    'DB_USER' : os.getenv('DB_USER'),
    'DB_PASSWORD' : os.getenv('DB_PASSWORD'),
    'DB_DATABASE' : os.getenv('DB_DATABASE'),

    'NEUCORE_BASE_URL' : os.getenv('API_BASE_URL'),
    'FINANCE_NEUCORE_KEY' : os.getenv('API_KEY'),
    'FINANCE_EVE_LOGIN' : os.getenv('API_EVE_LOGIN'),

    'ALL_TYPES_CORPORATIONS' : os.getenv('ALL_TYPES_CORPORATIONS')
}
JOURNAL_DATEFORMAT = "%Y-%m-%d %H:%M:%S"

class Wallets:

    def __init__(self, logger = None):
        if logger is None:
            self.logger = logging.getLogger('wallets')
            logging.basicConfig(
                filename='wallets.log',
                encoding='utf-8',
                level=os.getenv('UWSGI_LOG_LEVEL', 'ERROR').upper()
            )
        else:
            self.logger = logger
        self.__auth_header = {'Authorization': 'Bearer ' + env_vars['FINANCE_NEUCORE_KEY']}

        self.__check_env_vars()

        self.__db = mysql.connector.connect(
            host=env_vars['DB_HOST'],
            port=env_vars['DB_PORT'],
            user=env_vars['DB_USER'],
            password=env_vars['DB_PASSWORD'],
            database=env_vars['DB_DATABASE'],
        )

    def __check_env_vars(self):
        env_vars['NEUCORE_V2_BASE_URL'] = env_vars['NEUCORE_BASE_URL'] + '/api/app/v2/esi/latest'
        if (env_vars['ALL_TYPES_CORPORATIONS'] is not None and
            env_vars['ALL_TYPES_CORPORATIONS'] != ''
        ):
            env_vars['ALL_TYPES_CORPORATION_IDS'] = [
                int(x) for x in env_vars['ALL_TYPES_CORPORATIONS'].split(',')
            ]
        else:
            env_vars['ALL_TYPES_CORPORATION_IDS'] = []

    def run(self):
        self.__read_wallets()
        self.__db.close()

    def __read_wallets(self) -> None:
        cursor = self.__db.cursor()
        cursor.execute(
            """
                SELECT id, character_id, last_journal_date
                FROM corporations
                WHERE active = 1 AND character_id IS NOT NULL;
            """
        )
        corporation_data = cursor.fetchall()
        cursor.close()

        num_divisions = 7

        for data in corporation_data:
            corporation_id = data[0]
            for division in range(1, num_divisions + 1):
                div_journal_date = self.__read_wallet(
                    corporation_id = corporation_id,
                    character_id=data[1],
                    previous_journal_date=str(data[2] or ''),
                    division=division
                )
                if div_journal_date is None:
                    self.logger.warning('    Division %s: incomplete journal.', division)
                # preserve journal date logic for division 1
                if division == 1:
                    last_journal_date = div_journal_date
                # rate lim: 300 tokens / 15m, 200ok=2 tokens, 10/min, ~6 per
                time.sleep(7)
            if last_journal_date:
                self.logger.info('Read corporation %s wallet: Success.', corporation_id)
                cursor = self.__db.cursor()
                cursor.execute("UPDATE corporations SET last_journal_date = %s WHERE id = %s",
                               [last_journal_date, corporation_id])
                self.__db.commit()
            else:
                self.logger.info('Read corporation %s wallet: Failed to read complete journal.', corporation_id)

    def __read_wallet(self, corporation_id: int, character_id: int, previous_journal_date: str, division: int = 1, page: int = 1,
                      retry: int = 0) -> Optional[str]:
        """
        https://esi.evetech.net/ui/#/Wallet/get_corporations_corporation_id_wallets_division_journal
        30 days back, 3600 seconds (1h) cache
        """

        self.logger.info('    %s page %s ... ', division, page)

        request_time = datetime.datetime.now()

        url = (
            f'{env_vars['NEUCORE_V2_BASE_URL']}/corporations/{corporation_id}/'
            f'wallets/{division}/journal/?page={page}'
            f'&datasource={character_id}:{env_vars['FINANCE_EVE_LOGIN']}'
        )
        r = requests.get(url, headers=self.__auth_header, timeout=15)
        if r.status_code != 200:
            self.logger.error(
                'Request error: URL: %s: Status Code: %s, Reason: %s, Body: %s',
                url, r.status_code, r.reason, r.text
            )
            if r.status_code != 403 and retry < 2:
                self.logger.warning('retrying (%s) ...', retry + 1)
                return self.__read_wallet(
                    corporation_id,
                    character_id,
                    previous_journal_date,
                    division,
                    page,
                    retry + 1
                )
            return None
        pages = int(r.headers['X-Pages'])
        json = r.json()

        cursor = self.__db.cursor()
        first_journal_date = '9999-99-99 99:99:99'
        last_journal_date = ''
        for entry in json:
            # see also https://github.com/esi/eve-glue/blob/master/eve_glue/wallet_journal_ref.py
            if corporation_id not in env_vars['ALL_TYPES_CORPORATION_IDS'] and \
               entry['ref_type'] not in [
                    # tax income
                    'bounty_prizes', 
                    'ess_escrow_transfer',
                    'agent_mission_reward', 
                    'agent_mission_time_bonus_reward', 
                    'project_discovery_reward',
                    'daily_goal_payouts', 
                    'freelance_jobs_reward',
                    # isk removed from corp, (tax payments)
                    'corporation_account_withdrawal', 
                    # other types of interest ? not sure why not just all
                    'corporate_reward_payout',
                    'brokers_fee', 
                    'player_donation',
                    'jump_clone_activation_fee', 
                    'jump_clone_installation_fee', 
                    'structure_gate_jump',
                    'reprocessing_tax', 
                    'industry_job_tax',
                    'planetary_import_tax', 
                    'planetary_export_tax',
                    'office_rental_fee', 
            ]:
                continue

            journal_date = entry['date'].replace('T', ' ').replace('Z', '')
            if journal_date > last_journal_date:
                last_journal_date = journal_date
            if journal_date < first_journal_date:
                first_journal_date = journal_date

            year_month = (int(journal_date[0:4]) * 100) + int(journal_date[5:7])

            # amount, balance and tax is double in json but bigint in database
            sql = """
                INSERT IGNORE INTO wallet_journal (
                    id, 
                    corporation_id, 
                    ref_type, 
                    journal_date, 
                    journal_year_month, 
                    description, 
                    amount, 
                    reason, 
                    first_party_id, 
                    second_party_id, 
                    context_id_type, 
                    context_id,
                    division
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """
            data = [
                entry['id'],
                corporation_id,
                entry['ref_type'],
                journal_date,
                year_month,
                entry['description'],
                entry.get('amount', None),
                entry.get('reason', None),
                entry.get('first_party_id', None),
                entry.get('second_party_id', None),
                entry.get('context_id_type', None),
                entry.get('context_id', None),
                division
            ]
            cursor.execute(sql, data)
        self.__db.commit()
        cursor.close()

        # previous_journal_date is latest date we already have, api page order
        # is newest to oldest. can stop if we pass our previous_journal_date.
        if page < pages:
            # only apply to division 1 for now, has most entries.
            if division == 1:
                if previous_journal_date < first_journal_date:
                    self.__read_wallet(
                        corporation_id,
                        character_id,
                        previous_journal_date,
                        division,
                        page + 1
                    )
            else:
                self.__read_wallet(
                    corporation_id,
                    character_id,
                    previous_journal_date,
                    division,
                    page + 1
                )

        if last_journal_date == '':  # no bounties or mission rewards in journal
            return request_time.strftime(JOURNAL_DATEFORMAT)
        else:
            return last_journal_date

if __name__ == "__main__":
    wallets = Wallets()
    wallets.run()
