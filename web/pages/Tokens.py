"""
Corp finance tokens page and routes.
using env vars and mysql db, and neucore tokens.
"""
import os

import mysql.connector
import requests
import json
from typing import Union
from flask import render_template, url_for, session, Flask, request
from werkzeug.utils import redirect
from werkzeug.wrappers import Response as wzResponse
from datetime import datetime, timedelta

import copy
from wallets import Wallets
from tax_records import TaxRecords

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

env_vars = {
    'DB_HOST' : os.getenv('DB_HOST'),
    'DB_PORT' : os.getenv('DB_PORT', '3306'),
    'DB_USER' : os.getenv('DB_USER'),
    'DB_PASSWORD' : os.getenv('DB_PASSWORD'),
    'DB_DATABASE' : os.getenv('DB_DATABASE'),

    'ESI_BASE_URL' : 'https://esi.evetech.net/latest',
    'NEUCORE_BASE_URL' : os.getenv('API_BASE_URL'),
    'FINANCE_NEUCORE_KEY' : os.getenv('API_KEY'),
    'FINANCE_EVE_LOGIN' : os.getenv('API_EVE_LOGIN'),
    'FINANCE_MAILS_EVE_LOGIN' : os.getenv('FINANCE_MAILS_EVE_LOGIN'),
    'FINANCE_MAILS_CHAR_NAME' : os.getenv('FINANCE_MAILS_CHAR_NAME'),

    'CHECK_ALLIANCES' : os.getenv('CHECK_ALLIANCES'),
    'CHECK_CORPORATIONS' : os.getenv('CHECK_CORPORATIONS'),
}

class Tokens:
    """Tokens page and routes
    
        __corporations is a lookup dict for corp_id:int -> corp_data:dict
            - makes it very easy to find corp info
            - attributes match the sql database

            __corporations[98280055]["corporation_name"] -> "Second Sons"
    """
    __available_tokens = []

    __corporations = {}

    def __init__(self, app: Flask):
        self.__app = app
        self.__auth_header = {'Authorization': 'Bearer ' + env_vars['FINANCE_NEUCORE_KEY']}
        self.__wallets = Wallets()
        self.__tax_records = TaxRecords()
        self.__config = read_json_file("../config/tax_check_config.json")

        self.__check_env_vars()

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
                self.__app.logger.error(f'system environment variable {key} not configured')

        env_vars['NEUCORE_V1_BASE_URL'] = env_vars['NEUCORE_BASE_URL'] + '/api/app/v1/esi'
        env_vars['NEUCORE_V2_BASE_URL'] = env_vars['NEUCORE_BASE_URL'] + '/api/app/v2/esi'
        if env_vars['CHECK_ALLIANCES'] is not None and env_vars['CHECK_ALLIANCES'] != '':
            env_vars['CHECK_ALLIANCE_IDS'] = [
                int(x) for x in env_vars['CHECK_ALLIANCES'].split(',')
            ]
        else:
            env_vars['CHECK_ALLIANCE_IDS'] = []
        if env_vars['CHECK_CORPORATIONS'] is not None and env_vars['CHECK_CORPORATIONS'] != '':
            env_vars['CHECK_CORPORATION_IDS'] = [
                int(x) for x in env_vars['CHECK_CORPORATIONS'].split(',')
            ]
        else:
            env_vars['CHECK_CORPORATION_IDS'] = []

    def show(self) -> Union[str,wzResponse]:
        """Tokens page"""
        if 'character_id' not in session:
            return redirect(url_for('auth_login'))

        # start with database corporations
        cursor = self.__db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM corporations;")
        corporations = cursor.fetchall()
        cursor.close()

        current_date = datetime.now()
        prev_month_end = datetime(current_date.year, current_date.month, 1)
        prev_month_last_day = prev_month_end - timedelta(days=1)

        # convert into lookup dictionary for easier use
        for corp_dict in corporations:
            corp_dict["want"] = False
            self.__corporations[corp_dict["id"]] = corp_dict

        # check / add wanted alliances & corps in env vars
        for alliance_id in env_vars['CHECK_ALLIANCE_IDS']:
            self.__fetch_alliance_corporations(alliance_id)
        self.__add_new_corporations(env_vars['CHECK_CORPORATION_IDS'])

        # update ceo's and names
        self.__update_ceos()
        self.__update_names()
        self.__update_owner_names()

        # update database with new corp info
        self.__update_corporations_table()

        # sort dict by corp name for readable webpage
        self.__corporations = dict(sorted(
            self.__corporations.items(),
            key=lambda item: item[1]["corporation_name"]
        ))

        # add token info
        token_data_url = (
            f'{env_vars["NEUCORE_V1_BASE_URL"]}/eve-login/'
            f'{env_vars['FINANCE_EVE_LOGIN']}/token-data'
        )
        response = requests.get(token_data_url, headers=self.__auth_header, timeout=15)
        if response.status_code == 200:
            self.__available_tokens = response.json()
        else:
            self.__app.logger.error(response.content)
        for corp_id in self.__corporations:
            tokens = []
            for token in self.__available_tokens:
                if token['corporationId'] == corp_id:
                    tokens.append(token)
            self.__corporations[corp_id]["tokens"] = tokens

        # add tax balance
        for corp_id, corp in self.__corporations.items():
            corp["brave_tax_balance"] = self.__tax_records.get_brave_tax_balance(
                corp_id,
                prev_month_last_day
            )
            corp["starting_balance"] = self.__tax_records.get_brave_tax_balance(
                corp_id,
                datetime(year = 1, month = 1, day = 1)
            )

        # render page
        return render_template(
            'tokens.html',
            character_id=session['character_id'],
            alliance_ids=env_vars["CHECK_ALLIANCE_IDS"] + [0],
            has_token=self.__has_token,
            corporations=copy.deepcopy(self.__corporations),
            month_date = prev_month_last_day,
            show_starting_balance_editor = session.get("show_starting_balance_editor", True),
            show_owner_editor = session.get("show_owner_editor", True)
        )

    def show_starting_balance_editor(
        self,
    ) -> wzResponse:
        """route to toggle starting balance editor visibility"""
        if 'character_id' not in session:
            return redirect(url_for('auth_login'))
        status = request.form.get('status')
        session["show_starting_balance_editor"] = status == "1"
        return redirect(url_for('tokens'))
    def set_starting_balance(
        self,
    ) -> wzResponse:
        """route to set a specific corp attribute to a new value"""
        if 'character_id' not in session:
            return redirect(url_for('auth_login'))

        corp_id = request.form.get('corporation_id')
        try:
            corp_id = int(corp_id)
        except ValueError as e:
            self.__app.logger.error(e)

        starting_balance = int(request.form.get('starting_balance'))
        self.__corporations[corp_id]['starting_balance'] = starting_balance
        self.__tax_records.insert_tax_record(
            {
                "corporation_id": corp_id,
                "tax_month_date": datetime(year=1, month=1, day=1),
                "taxable_income": 0,
                "corp_tax_amount": 0,
                "brave_tax_amount": 0,
                "brave_tax_payments": starting_balance
            }
        )
        return redirect(url_for('tokens'))

    def show_owner_editor(
        self,
    ) -> wzResponse:
        """route to toggle owner editor visibility"""
        if 'character_id' not in session:
            return redirect(url_for('auth_login'))
        status = request.form.get('status')
        session["show_owner_editor"] = status == "1"
        return redirect(url_for('tokens'))

    def set_corp_attr(
        self,
    ) -> wzResponse:
        """route to set a specific corp attribute to a new value"""
        if 'character_id' not in session:
            return redirect(url_for('auth_login'))

        corp_id = request.form.get('corporation_id')
        attribute_name = request.form.get('attribute_name')
        attribute_value = request.form.get('attribute_value')
        self.__set_corp_attr(corp_id, attribute_name, attribute_value)

        # triggers related change
        if attribute_name == 'active' and attribute_value == '0':
            self.__set_corp_attr(corp_id, 'is_taxed', 0)
        if attribute_name == 'corporation_owner_name':
            if attribute_value == "":
                self.__set_corp_attr(corp_id, 'corporation_owner_id', None)
            else:
                owner_id = self.__get_character_id(attribute_value)
                self.__set_corp_attr(corp_id, 'corporation_owner_id', owner_id)

        return redirect(url_for('tokens'))
    def __set_corp_attr(
        self,
        corp_id,
        attribute_name,
        attribute_value
    ) -> None:
        try:
            corp_id = int(corp_id)
        except ValueError as e:
            self.__app.logger.error(e)
        # update local data
        self.__corporations[corp_id][attribute_name] = attribute_value

        # skip fields that are local data only
        if attribute_name in [
            "corporation_owner_name",
            "corporation_ceo_name"
        ]:
            return

        # update db data
        cursor = self.__db.cursor()
        sql = f"""
            UPDATE corporations SET {attribute_name} = %s
            WHERE id = %s;
        """
        cursor.execute(sql, [attribute_value, corp_id])
        self.__db.commit()
        cursor.close()

    def __add_new_corporations(self, corp_ids, alliance_id=0) -> None:
        for corp_id in corp_ids:
            if self.__corporations.get(corp_id, None) is not None:
                # was already added, still wanted
                self.__corporations[corp_id]["want"] = True

                if alliance_id != 0 and alliance_id != self.__corporations[corp_id]["alliance_id"]:
                    # we know corp changed alliance, update
                    self.__corporations[corp_id]["alliance_id"] = alliance_id
                continue

            # corp not in database yet, add what we know
            self.__corporations[corp_id] = {
                "id": corp_id, 
                "alliance_id": alliance_id, 
                "want": True,
                "character_id": None,
                "last_journal_date": None,
                "active": 0,
                "is_alt_corp": 0,
                "is_taxed": 1,
                "corporation_owner_id": None,
                "corporation_ceo_id": None,
                "brave_tax_balance": 0,
            }

    def __fetch_alliance_corporations(self, alliance_id) -> None:
        # return {99003214: [98024275], 99010079: [98112599, 98209548]}
        url = f'{env_vars['ESI_BASE_URL']}/alliances/{alliance_id}/corporations/'
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            self.__add_new_corporations(response.json(), alliance_id)
        else:
            self.__app.logger.error(response.content)

    def __update_names(self) -> None:
        # update names for corps and corp ceos
        corporation_ids = list(self.__corporations)
        ceo_id_corp_lookup = {}
        for k,v in [
            (corp["corporation_ceo_id"],corp["id"])
            for corp in self.__corporations.values()
            if corp["corporation_ceo_id"] is not None
        ]:
            ceo_id_corp_lookup[k] = v

        response = requests.post(
            url=f'{env_vars['ESI_BASE_URL']}/universe/names/',
            json=corporation_ids+list(ceo_id_corp_lookup),
            timeout=15
        )
        if response.status_code == 200:
            for item in response.json():
                if item['category'] == 'corporation':
                    self.__corporations[item['id']]["corporation_name"] = item['name']
                if item['category'] == 'character':
                    corp_id = ceo_id_corp_lookup[item["id"]]
                    self.__corporations[corp_id]["corporation_ceo_name"] = item["name"]
        else:
            self.__app.logger.error(response.content)

    def __update_owner_names(self):
        # slightly different because owner is not guaranteed unique (ceo was)
        owner_names = {}
        for corp in self.__corporations.values():
            if corp["corporation_owner_id"] is not None:
                owner_names[corp["corporation_owner_id"]] = None
        url = f'{env_vars['ESI_BASE_URL']}/universe/names'
        response = requests.post(url, json=list(owner_names), timeout=15)
        if response.status_code == 200:
            for item in response.json():
                if item['category'] == 'character':
                    owner_names[item["id"]] = item["name"]
        else:
            self.__app.logger.error(response.content)
        for corp in self.__corporations.values():
            if corp["corporation_owner_id"] is not None:
                corp["corporation_owner_name"] = owner_names[corp["corporation_owner_id"]]

    def update_ceos(self) -> wzResponse:
        """Route for updating all CEOs, can take a while"""
        if 'character_id' not in session:
            return redirect(url_for('auth_login'))
        self.__update_ceos(missing_only=False, limit=None)
        self.__update_corporations_table()
        return redirect(url_for('tokens'))

    def __update_ceos(
        self,
        missing_only = True,
        limit: Union[int, None] = 5
    ) -> None:
        """
            default to missing only andlimit to 5 for new entries because 
            it is slow and we probably just want the page to load first.
        """
        for corp_id, corp in self.__corporations.items():
            if not missing_only or corp["corporation_ceo_id"] is None:
                url = f'{env_vars['ESI_BASE_URL']}/corporations/{corp_id}'
                response = requests.get(url, timeout=15)
                if response.status_code == 200:
                    corp_info = response.json()
                    corp["corporation_ceo_id"] = corp_info["ceo_id"]
                    # also set these while we have the info result
                    corp["corporation_name"] = corp_info["name"]
                    corp["alliance_id"] = corp_info.get("alliance_id", 0)
                else:
                    self.__app.logger.error(response.content)
            if limit is not None:
                limit -= 1
                if limit < 1:
                    return

    def __update_corporations_table(self):
        """
            Updates the corporations table with the version in memory. 

            Effectively this only adds new corps from the check_ env vars, 
            the database table should already match for existing entry.
            But the insert .. update makes this more error proof and 
            potentially re-usable for corp checks in the future
        """
        corporations_columns = [
            "id",
            "alliance_id",
            "is_alt_corp",
            "is_taxed",
            "corporation_name",
            "corporation_owner_id",
            "corporation_ceo_id",
            "character_id",
            "last_journal_date",
            "active"
        ]
        corp_data = []
        for corp in self.__corporations.values():
            corp_data.append([corp[column] for column in corporations_columns])
        update = ", ".join(
            [
                f"{col_name} = VALUES({col_name})"
                for col_name in corporations_columns[1:]
            ]
        )

        cursor = self.__db.cursor()
        sql = f"""
            INSERT INTO corporations ({", ".join(corporations_columns)})
            VALUES ({"%s, "* (len(corporations_columns)-1) + "%s"})
            ON DUPLICATE KEY UPDATE {update};
        """
        cursor.executemany(sql, corp_data)
        self.__db.commit()
        cursor.close()

    def __get_character_id(self, character_name):
        url = f'{env_vars['ESI_BASE_URL']}/universe/ids'
        response = requests.post(url, json=[character_name], timeout=15)
        if response.status_code == 200:
            character_ids = response.json().get("characters", [])
        else:
            self.__app.logger.error(response.content)
            character_ids = []
        for res in character_ids:
            return res["id"]

    def __has_token(self, corporation_id: int, character_id: int) -> bool:
        for token in self.__corporations[corporation_id]["tokens"]:
            if token['characterId'] == character_id:
                return True
        return False

    def update_wallets(self) -> wzResponse:
        """update database wallet_journals"""
        if 'character_id' not in session:
            return redirect(url_for('auth_login'))
        self.__wallets.run()
        return redirect(url_for('tokens'))

    def update_tax_records(self) -> wzResponse:
        """update database wallet_journals"""
        if 'character_id' not in session:
            return redirect(url_for('auth_login'))
        year = int(request.form.get('year'))
        month = int(request.form.get('month'))
        self.__tax_records.update_tax_records(year, month)
        return redirect(url_for('tokens'))

    def tax_evemails(self, balance_threshold: Union[int, None] = None) -> wzResponse:
        """Send tax reminders to every corp"""
        if 'character_id' not in session:
            return redirect(url_for('auth_login'))
        threshold_str = request.form.get('threshold', None)
        if threshold_str is not None:
            try:
                balance_threshold = int(threshold_str)
            except ValueError:
                return redirect(url_for('tokens'))
        self.__tax_records.send_tax_evemails(balance_threshold)
        return redirect(url_for('tokens'))

    def test_mail(self) -> wzResponse:
        """send a test mail"""
        if 'character_id' not in session:
            return redirect(url_for('auth_login'))
        receiver_id = int(request.form.get('receiver_id'))
        sender_id = int(request.form.get('sender_id'))
        recipients=[{
            "recipient_id": receiver_id,
            "recipient_type": "character"
        }]

        evemail_endpoint = (
            f"{env_vars['NEUCORE_V2_BASE_URL']}/characters/{sender_id}/mail/"
            f"?datasource="f"{sender_id}:{env_vars['FINANCE_MAILS_EVE_LOGIN']}"
        )
        mail_info = {
            "approved_cost": 0,
            "body": f"Hello {receiver_id}",
            "recipients": recipients,
            "subject": "Finance Check Test Mail"
        }
        self.__app.logger.info(
            f"Sending test mail to {receiver_id} with "
            f"{env_vars['FINANCE_EVE_LOGIN']} token ({self.__auth_header}) "
            f"at {self.__available_tokens}"
        )
        response = requests.post(
            url = evemail_endpoint,
            data=json.dumps(mail_info),
            headers=self.__auth_header,
            timeout=15
        )
        if response.status_code == 201:
            self.__app.logger.info(f"Test mail sent to {receiver_id}")
        else:
            info_str = f"{response.status_code}, {response.headers}, {response.content}"
            self.__app.logger.error(info_str)
        return redirect(url_for('tokens'))
