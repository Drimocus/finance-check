import datetime
import os
from typing import Optional

import mysql.connector
import requests


class FetchWallet:

    def __init__(self):
        self.__base_url = os.getenv('API_BASE_URL') + '/api/app/v2/esi/latest'
        self.__auth_header = {'Authorization': 'Bearer ' + os.getenv('API_KEY')}
        self.__login_name = os.getenv('API_EVE_LOGIN')
        self.__db = mysql.connector.connect(
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT', 3306),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            database=os.getenv('DB_DATABASE'),
        )
        self.__all_types_corporations = [int(x) for x in os.getenv('ALL_TYPES_CORPORATIONS').split(',')]

    def run(self):
        self.__read_wallets()
        self.__db.close()

    def __read_wallets(self) -> None:
        cursor = self.__db.cursor()
        cursor.execute("SELECT id, character_id, last_journal_date FROM corporations WHERE active = 1")
        corporation_data = cursor.fetchall()
        cursor.close()

        num_divisions = 7

        for data in corporation_data:
            corporation_id = data[0]
            print('Read corporation {} wallet:'.format(corporation_id))
            for division in range(1, num_divisions + 1):
                div_journal_date = self.__read_wallet(
                    corporation_id = corporation_id, 
                    character_id=data[1], 
                    previous_journal_date=str(data[2] or ''), 
                    division=division
                )
                if div_journal_date is None:
                    print(f'    Division {division}: incomplete journal.')
                # preserve journal date logic for division 1
                if division == 1:
                    last_journal_date = div_journal_date
            if last_journal_date:
                print('    Success.')
                cursor = self.__db.cursor()
                cursor.execute("UPDATE corporations SET last_journal_date = %s WHERE id = %s",
                               [last_journal_date, corporation_id])
                self.__db.commit()
            else:
                print('    Failed to read complete journal.')

    def __read_wallet(self, corporation_id: int, character_id: int, previous_journal_date: str, division: int = 1, page: int = 1,
                      retry: int = 0) -> Optional[str]:
        """
        https://esi.evetech.net/ui/#/Wallet/get_corporations_corporation_id_wallets_division_journal
        30 days back, 3600 seconds (1h) cache
        """

        print('    {} page {} ... '.format(division, page))

        request_time = datetime.datetime.now()

        url = '{}/corporations/{}/wallets/{}/journal/?page={}&datasource={}:{}'.format(
            self.__base_url, corporation_id, division, page, character_id, self.__login_name)
        r = requests.get(url, headers=self.__auth_header)
        if r.status_code != 200:
            print('Request error: URL: {}: Status Code: {}, Reason: {}, Body: {}'
                  .format(url, r.status_code, r.reason, r.text))
            if r.status_code != 403 and retry < 2:
                print('retrying ({}) ...'.format(retry + 1))
                return self.__read_wallet(corporation_id, character_id, previous_journal_date, division, page, retry + 1)
            return None
        pages = int(r.headers['X-Pages'])
        json = r.json()

        cursor = self.__db.cursor()
        first_journal_date = '9999-99-99 99:99:99'
        last_journal_date = ''
        for entry in json:
            # see also https://github.com/esi/eve-glue/blob/master/eve_glue/wallet_journal_ref.py
            if corporation_id not in self.__all_types_corporations and \
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
                    self.__read_wallet(corporation_id, character_id, previous_journal_date, division, page + 1)
            else:
                self.__read_wallet(corporation_id, character_id, previous_journal_date, division, page + 1)

        if last_journal_date == '':  # no bounties or mission rewards in journal
            return request_time.strftime('%Y-%m-%d %H:%M:%S')
        else:
            return last_journal_date
