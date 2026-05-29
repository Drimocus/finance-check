import os
from typing import Union

import mysql.connector
import requests
from flask import render_template, Response, url_for, session, Flask, request
from werkzeug.utils import redirect


class Tokens:
    __corporation_names = {}

    __configured_corporations = []

    __want_corporations = []

    __available_tokens = []

    __corporations = {}

    def __init__(self, app: Flask):
        self.__app = app
        self.__esi_base_url = 'https://esi.evetech.net/latest'
        self.__core_base_url = os.getenv('API_BASE_URL') + '/api/app'
        self.__auth_header = {'Authorization': 'Bearer ' + os.getenv('API_KEY')}
        self.__login_name = os.getenv('API_EVE_LOGIN')
        self.__check_alliances = os.getenv('CHECK_ALLIANCES')
        self.__check_corporations = os.getenv('CHECK_CORPORATIONS')

        self.__db = mysql.connector.connect(
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT', 3306),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            database=os.getenv('DB_DATABASE'),
        )

    def show(self) -> Union[str, Response]:
        if 'character_id' not in session:
            return redirect(url_for('auth_login'))

        self.__want_corporations = self.__fetch_alliance_corporations()
        if self.__check_corporations:
            self.__want_corporations[0] = [int(x) for x in self.__check_corporations.split(',')]

        all_want_corporation_ids = []
        for alliance_id in self.__want_corporations.keys():
            all_want_corporation_ids = all_want_corporation_ids + self.__want_corporations[alliance_id]
        self.__fetch_names(all_want_corporation_ids)

        cursor = self.__db.cursor(dictionary=True)
        cursor.execute("SELECT id, corporation_name, character_id, last_journal_date, active, alliance_id, is_alt_corp, corporation_ceo_id, corporation_owner_id FROM corporations")
        self.__configured_corporations = cursor.fetchall()
        cursor.close()

        url = '{}/v1/esi/eve-login/{name}/token-data'.format(self.__core_base_url, name=self.__login_name)
        response = requests.get(url, headers=self.__auth_header)
        if response.status_code == 200:
            self.__available_tokens = response.json()
        else:
            self.__app.logger.error(response.content)
        
        for corp_dict in self.__configured_corporations:
            corp_dict["want"] = corp_dict["id"] in all_want_corporation_ids
            self.__corporations[corp_dict["id"]] = corp_dict
        for alliance_id, corp_ids in self.__want_corporations.items():
            for corp_id in corp_ids:
                if corp_id not in self.__corporations:
                    self.__corporations[corp_id] = {
                        "id": corp_id,
                        "alliance_id": alliance_id,
                        "corporation_name": self.__corporation_names[corp_id],
                        "character_id": None,
                        "last_journal_date": None,
                        "active": None,
                        "is_alt_corp": None,
                        "want": True,
                        "corporation_owner_id": None,
                        "corporation_ceo_id": None,
                    }

        return render_template(
            'tokens.html',
            character_id=session['character_id'],
            want_corporations=self.__want_corporations,
            configured_corporations=self.__configured_corporations,
            is_want_corporation=self.__is_want_corporation,
            find_available_tokens=self.__find_available_tokens,
            has_token=self.__has_token,
            corporations=self.__corporations
        )

    def add(self) -> Union[str, Response]:
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

    def deactivate(self) -> Union[str, Response]:
        if 'character_id' not in session:
            return redirect(url_for('auth_login'))
        return self.__update_active(0)

    def activate(self) -> Union[str, Response]:
        if 'character_id' not in session:
            return redirect(url_for('auth_login'))
        return self.__update_active(1)

    def __update_active(self, active: int) -> Union[str, Response]:
        cursor = self.__db.cursor()

        sql = "UPDATE corporations SET active = %s WHERE id = %s"
        data = [active, request.form.get('corporation_id')]
        cursor.execute(sql, data)
        self.__db.commit()

        cursor.close()
        return redirect(url_for('tokens'))

    def unset_alt_corp(self) -> Union[str, Response]:
        if 'character_id' not in session:
            return redirect(url_for('auth_login'))
        return self.__update_alt_corp(0)

    def set_alt_corp(self) -> Union[str, Response]:
        if 'character_id' not in session:
            return redirect(url_for('auth_login'))
        return self.__update_alt_corp(1)

    def __update_alt_corp(self, active: int) -> Union[str, Response]:
        cursor = self.__db.cursor()

        sql = "UPDATE corporations SET is_alt_corp = %s WHERE id = %s"
        data = [active, request.form.get('corporation_id')]
        cursor.execute(sql, data)
        self.__db.commit()

        cursor.close()
        return redirect(url_for('tokens'))

    def __fetch_alliance_corporations(self) -> dict:
        # return {99003214: [98024275], 99010079: [98112599, 98209548]}
        want_alliance_corporations = {}
        if self.__check_alliances:
            for alliance_id in [int(x) for x in self.__check_alliances.split(',')]:
                url = '{}/alliances/{alliance_id}/corporations/'.format(self.__esi_base_url, alliance_id=alliance_id)
                response = requests.get(url)
                if response.status_code == 200:
                    want_alliance_corporations[alliance_id] = response.json()
                else:
                    self.__app.logger.error(response.content)
        return want_alliance_corporations

    def __fetch_names(self, corporation_ids: []) -> None:
        """self.__corporation_names = {
           98024275: 'Rational Chaos Inc.', 98112599: 'Black Queen Enterprises', 98209548: 'Brave Little Toaster.',
           98645283: 'Brave United Holding', 98599810: 'Brave Nubs'}
        return"""
        url = '{}/universe/names/'.format(self.__esi_base_url)
        response = requests.post(url, json=corporation_ids)  # Note: corporation_ids cannot have more than 1000 items
        if response.status_code == 200:
            for item in response.json():
                if item['category'] == 'corporation':
                    self.__corporation_names[item['id']] = item['name']
        else:
            self.__app.logger.error(response.content)

    def __is_want_corporation(self, corporation_id: int) -> bool:
        for alliance_id in self.__want_corporations.keys():
            for want_corporation_id in self.__want_corporations[alliance_id]:
                if want_corporation_id == corporation_id:
                    return True
        return False

    def __find_available_tokens(self, corporation_id: int) -> list:
        tokens = []
        for token in self.__available_tokens:
            if token['corporationId'] == corporation_id:
                tokens.append(token)
        return tokens

    def __has_token(self, corporation_id: int, character_id: int) -> bool:
        for token in self.__available_tokens:
            if token['corporationId'] == corporation_id and token['characterId'] == character_id:
                return True
        return False
