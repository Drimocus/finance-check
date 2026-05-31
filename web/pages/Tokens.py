"""
Corp finance tokens page and routes.
using env vars and mysql db, and neucore tokens.
"""
import os

import mysql.connector
import requests
from flask import render_template, url_for, session, Flask, request
from werkzeug.utils import redirect
from werkzeug.wrappers import Response as wzResponse

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
        self.__esi_base_url = 'https://esi.evetech.net/latest'
        self.__core_base_url = os.getenv('API_BASE_URL') + '/api/app'
        self.__auth_header = {'Authorization': 'Bearer ' + os.getenv('API_KEY')}
        self.__login_name = os.getenv('API_EVE_LOGIN')

        check_alliances_str = os.getenv('CHECK_ALLIANCES')
        if check_alliances_str is not None:
            self.__check_alliance_ids = [int(x) for x in check_alliances_str.split(',')]
        else:
            self.__check_alliance_ids = []
        check_corporations_str = os.getenv('CHECK_CORPORATIONS')
        if check_corporations_str is not None:
            self.__check_corporation_ids = [int(x) for x in check_corporations_str.split(',')]
        else:
            self.__check_corporation_ids = []

        self.__db = mysql.connector.connect(
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT', '3306'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            database=os.getenv('DB_DATABASE'),
        )

    def show(self) -> str:
        """Tokens page"""
        if 'character_id' not in session:
            return redirect(url_for('auth_login'))

        # start with database corporations
        cursor = self.__db.cursor(dictionary=True)
        cursor.execute("SELECT id, corporation_name, character_id, last_journal_date, active, alliance_id, is_alt_corp, is_taxed, corporation_ceo_id, corporation_owner_id FROM corporations")
        corporations = cursor.fetchall()
        cursor.close()

        # convert into lookup dictionary for easier use
        for corp_dict in corporations:
            corp_dict["want"] = False
            self.__corporations[corp_dict["id"]] = corp_dict

        # check / add wanted alliances & corps in env vars
        for alliance_id in self.__check_alliance_ids:
            self.__fetch_alliance_corporations(alliance_id)
        self.__add_new_corporations(self.__check_corporation_ids)

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
        url = f'{self.__core_base_url}/v1/esi/eve-login/{self.__login_name}/token-data'
        response = requests.get(url, headers=self.__auth_header, timeout=15)
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
        last_tax_records = self.__last_tax_records()
        for tax_record in last_tax_records:
            self.__corporations[
                tax_record["corporation_id"]
            ]["brave_tax_balance"] = tax_record["brave_tax_balance"]

        # render page
        return render_template(
            'tokens.html',
            character_id=session['character_id'],
            alliance_ids=self.__check_alliance_ids + [0],
            has_token=self.__has_token,
            corporations=self.__corporations
        )

    def set_corp_attr(
        self,
    ) -> wzResponse:
        """route to set a specific corp attribute to a new value"""
        corp_id = request.form.get('corporation_id')
        attribute_name = request.form.get('attribute_name')
        attribute_value = request.form.get('attribute_value')
        self.__set_corp_attr(corp_id, attribute_name, attribute_value)

        # triggers related change
        if attribute_name == 'active' and attribute_value == '0':
            self.__set_corp_attr(corp_id, 'is_taxed', 0)
        if attribute_name == 'corporation_owner_name':
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
            UPDATE corporations SET {attribute_name} = {attribute_value}
            WHERE id = {corp_id}
        """
        cursor.execute(sql)
        self.__db.commit()
        cursor.close()

    def __last_tax_records(self) -> list[dict]:
        cursor = self.__db.cursor(dictionary=True)
        sql = """
            SELECT corporation_id, brave_tax_balance, tax_month_date
            FROM tax_records as data 
            WHERE tax_month_date = (
                SELECT MAX(tax_month_date)
                FROM tax_records
                WHERE corporation_id = data.corporation_id
            );
        """
        cursor.execute(sql)
        results = cursor.fetchall()
        cursor.close()
        return results

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
                "active": 1,
                "is_alt_corp": 0,
                "is_taxed": 1,
                "corporation_owner_id": None,
                "corporation_ceo_id": None,
                "brave_tax_balance": 0,
            }

    def __fetch_alliance_corporations(self, alliance_id) -> None:
        # return {99003214: [98024275], 99010079: [98112599, 98209548]}
        url = f'{self.__esi_base_url}/alliances/{alliance_id}/corporations/'
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
            url=f'{self.__esi_base_url}/universe/names/',
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
        url = f'{self.__esi_base_url}/universe/names'
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

    def __update_ceos(self, missing_only=True, limit=10) -> None:
        """limit to 10 for test because it is slow / rate limit sketchy"""
        for corp_id, corp in self.__corporations.items():
            if not missing_only or corp["corporation_ceo_id"] is None:
                url = f'{self.__esi_base_url}/corporations/{corp_id}'
                response = requests.get(url, timeout=15)
                if response.status_code == 200:
                    corp_info = response.json()
                    corp["corporation_ceo_id"] = corp_info["ceo_id"]
                    # also set these while we have the info result
                    corp["corporation_name"] = corp_info["name"]
                    corp["alliance_id"] = corp_info.get("alliance_id", 0)
                else:
                    self.__app.logger.error(response.content)
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
        url = f'{self.__esi_base_url}/universe/ids'
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
