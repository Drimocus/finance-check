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

        # sort dict by corp name for readable webpage
        self.__corporations = dict(sorted(
            self.__corporations.items(), 
            key=lambda item: item[1]["corporation_name"]
        ))

        # add token info
        url = f'{self.__core_base_url}/v1/esi/eve-login/{self.__login_name}/token-data'
        response = requests.get(url, headers=self.__auth_header)
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
            self.__corporations[tax_record["corporation_id"]]["brave_tax_balance"] = tax_record["brave_tax_balance"]

        # render page
        return render_template(
            'tokens.html',
            character_id=session['character_id'],
            alliance_ids=self.__check_alliance_ids,
            has_token=self.__has_token,
            corporations=self.__corporations
        )

    def add(self) -> wzResponse:
        """add corp route"""
        if 'character_id' not in session:
            return redirect(url_for('auth_login'))
        cursor = self.__db.cursor()

        sql = "INSERT INTO corporations " \
              "(id, corporation_name, character_id, active) " \
              "VALUES (%s, %s, %s, 1) " \
              "ON DUPLICATE KEY UPDATE character_id = %s, active = 1"
        data = [request.form.get('corporation_id'), request.form.get('corporation_name'),
                request.form.get('character_id'), request.form.get('character_id')]
        cursor.execute(sql, data)
        self.__db.commit()

        cursor.close()
        return redirect(url_for('tokens'))

    def set_corp_attr(self) -> wzResponse:
        """route to set a specific corp attribute to a new value"""
        corp_id = request.form.get('corporation_id')
        attribute_name = request.form.get('attribute_name')
        attribute_value = request.form.get('attribute_value')

        cursor = self.__db.cursor()
        sql = f"""
            UPDATE corporations SET {attribute_name} = {attribute_value}
            WHERE id = {corp_id}
        """
        cursor.execute(sql)
        self.__db.commit()
        cursor.close()
        return redirect(url_for('tokens'))

    def __last_tax_records(self) -> list[dict]:
        cursor = self.__db.cursor(dictionary=True)
        sql = "select corporation_id, brave_tax_balance, tax_month_date FROM tax_records as data WHERE tax_month_date = (SELECT MAX(tax_month_date) FROM tax_records WHERE corporation_id = data.corporation_id);"
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
        for k,v in [(corp["corporation_ceo_id"],corp["id"]) for corp in self.__corporations.values()]:
            if k is not None:
                ceo_id_corp_lookup[k] = v
        corporation_ceo_ids = list(ceo_id_corp_lookup)

        url = '{}/universe/names/'.format(self.__esi_base_url)
        response = requests.post(url, json=corporation_ids+corporation_ceo_ids)
        # Note: corporation_ids cannot have more than 1000 items
        if response.status_code == 200:
            for item in response.json():
                if item['category'] == 'corporation':
                    self.__corporations[item['id']]["corporation_name"] = item['name']
                if item['category'] == 'character':
                    corp_id = ceo_id_corp_lookup[item["id"]]
                    self.__corporations[corp_id]["corporation_ceo_name"] = item["name"]
        else:
            self.__app.logger.error(response.content)
        
        owner_id_corp_lookup = {}
        for k,v in [(corp["corporation_owner_id"],corp["id"]) for corp in self.__corporations.values()]:
            if k is not None:
                owner_id_corp_lookup[k] = v
        corporation_owner_ids = list(owner_id_corp_lookup)
        url = '{}/universe/names/'.format(self.__esi_base_url)
        response = requests.post(url, json=corporation_owner_ids)
        # Note: corporation_ids cannot have more than 1000 items
        if response.status_code == 200:
            for item in response.json():
                if item['category'] == 'character':
                    corp_id = owner_id_corp_lookup[item["id"]]
                    self.__corporations[corp_id]["corporation_owner_name"] = item["name"]
        else:
            self.__app.logger.error(response.content)

    def __update_ceos(self, missing_only=True, limit=10) -> None:
        """limit to 10 for test because it is slow / rate limit sketchy"""
        for corp_id, corp in self.__corporations.items():
            if not missing_only or corp["corporation_ceo_id"] is None:
                url = f'{self.__esi_base_url}/corporations/{corp_id}'
                response = requests.get(url)
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

    def __has_token(self, corporation_id: int, character_id: int) -> bool:
        for token in self.__corporations[corporation_id]["tokens"]:
            if token['characterId'] == character_id:
                return True
        return False
