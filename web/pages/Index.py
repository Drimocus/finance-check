import csv
import datetime
import io
import os
from typing import Union

import mysql.connector
from flask import render_template, session, redirect, url_for, Response, request


class Index:

    def __init__(self):
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

        # request params
        current_year = datetime.datetime.now().year
        query_params = {
            'mode': request.args.get('mode', 'details'),
            'corporation': self.__int(request.args.get('corporation', 0)),
            'year': self.__int(request.args.get('year', current_year)),
            'month': self.__int(request.args.get('month', datetime.datetime.now().month)),
            'type_all': request.args.get('type_all', '0'),
            'type_bounty': request.args.get('type_bounty', '0'),
            'type_ess_escrow': request.args.get('type_ess_escrow', '0'),
            'type_mission_reward': request.args.get('type_mission_reward', '0'),
            'type_corporate_reward': request.args.get('type_corporate_reward', '0'),
            'type_brokers_fee': request.args.get('type_brokers_fee', '0'),
            'type_player_donation': request.args.get('type_player_donation', '0'),
            'type_jump_clone': request.args.get('type_jump_clone', '0'),
            'type_structure_gate_jump': request.args.get('type_structure_gate_jump', '0'),
            'type_reprocessing': request.args.get('type_reprocessing', '0'),
            'type_industry_job': request.args.get('type_industry_job', '0'),
            'type_planetary': request.args.get('type_planetary', '0'),
            'type_office_rental': request.args.get('type_office_rental', '0'),
            'type_project_discovery': request.args.get('type_project_discovery', '0'),
        }

        # ref types
        types = []
        types_placeholder = []
        if query_params['type_bounty'] == '1':
            types.append('bounty_prizes')
            types_placeholder.append('%s')
        if query_params['type_ess_escrow'] == '1':
            types.append('ess_escrow_transfer')
            types_placeholder.append('%s')
        if query_params['type_mission_reward'] == '1':
            types.append('agent_mission_reward')
            types.append('agent_mission_time_bonus_reward')
            types_placeholder.append('%s')
            types_placeholder.append('%s')
        if query_params['type_corporate_reward'] == '1':
            types.append('corporate_reward_payout')
            types_placeholder.append('%s')
        if query_params['type_brokers_fee'] == '1':
            types.append('brokers_fee')
            types_placeholder.append('%s')
        if query_params['type_player_donation'] == '1':
            types.append('player_donation')
            types_placeholder.append('%s')
        if query_params['type_jump_clone'] == '1':
            types.append('jump_clone_activation_fee')
            types.append('jump_clone_installation_fee')
            types_placeholder.append('%s')
            types_placeholder.append('%s')
        if query_params['type_structure_gate_jump'] == '1':
            types.append('structure_gate_jump')
            types_placeholder.append('%s')
        if query_params['type_reprocessing'] == '1':
            types.append('reprocessing_tax')
            types_placeholder.append('%s')
        if query_params['type_industry_job'] == '1':
            types.append('industry_job_tax')
            types_placeholder.append('%s')
        if query_params['type_planetary'] == '1':
            types.append('planetary_export_tax')
            types.append('planetary_import_tax')
            types_placeholder.append('%s')
            types_placeholder.append('%s')
        if query_params['type_office_rental'] == '1':
            types.append('office_rental_fee')
            types_placeholder.append('%s')
        if query_params['type_project_discovery'] == '1':
            types.append('project_discovery_reward')
            types_placeholder.append('%s')

        # journal year and month
        year_month = (query_params['year'] * 100) + query_params['month']

        journal = []
        if query_params['mode'] == 'details':
            journal = self.__fetch_details(query_params['corporation'], year_month, types, types_placeholder,
                                           query_params['type_all'])
        elif query_params['mode'] == 'sum_months':
            journal = self.__fetch_sum_months(query_params['corporation'], year_month, types, types_placeholder,
                                              query_params['type_all'])
        elif query_params['mode'] == 'sum_corporations':
            journal = self.__fetch_sum_corporations(year_month, types, types_placeholder, query_params['type_all'])

        sum_amount_in = 0
        sum_amount_out = 0
        for entry in journal:
            if entry['amount_in']:
                sum_amount_in += entry['amount_in']
            if entry['amount_out']:
                sum_amount_out += entry['amount_out']

        # fetch corporations
        cursor = self.__db.cursor(dictionary=True)
        cursor.execute("SELECT id, corporation_name, last_journal_date FROM corporations "
                       "WHERE active = 1 ORDER BY corporation_name")
        corporations_active = cursor.fetchall()
        cursor.execute("SELECT id, corporation_name, last_journal_date FROM corporations "
                       "WHERE active = 0 ORDER BY corporation_name")
        corporations_inactive = cursor.fetchall()
        cursor.close()

        self.__db.close()

        if request.args.get('export', '0') == '1':
            if query_params['mode'] == 'details':
                filename = "{}-{}-{:02d}-{}".format(query_params['mode'], query_params['year'], query_params['month'],
                                                    query_params['corporation'])
            elif query_params['mode'] == 'sum_months':
                filename = "{}-{}".format(query_params['mode'], query_params['corporation'])
            else:  # sum_corporations
                filename = "{}-{}-{:02d}".format(query_params['mode'], query_params['year'], query_params['month'])

            return Response(
                self.__create_csv(params=query_params, journal=journal),
                mimetype='text/csv',
                headers={'Content-disposition': 'attachment; filename=' + filename + '.csv'}
            )
        else:
            return render_template(
                'index.html', character_id=session['character_id'], current_year=current_year, params=query_params,
                corporations_active=corporations_active, corporations_inactive=corporations_inactive,
                journal=journal, sum_amount_in=sum_amount_in, sum_amount_out=sum_amount_out
            )

    def __fetch_details(self, corporation: int, year_month: int, types: [],
                        types_placeholder: [], type_all: str) -> []:
        journal = []
        if len(types) > 0 or type_all == '1':
            cursor = self.__db.cursor(dictionary=True)
            sql = "SELECT ref_type, journal_date, description, reason, " \
                  "CASE WHEN amount >= 0 THEN amount END AS amount_in, " \
                  "CASE WHEN amount < 0 THEN amount END AS amount_out " \
                  "FROM wallet_journal " \
                  "WHERE corporation_id = %s AND journal_year_month = %s AND division = 1 AND ref_type NOT IN ('daily_goal_payouts', 'corporation_account_withdrawal', 'freelance_jobs_reward') "
            params = [corporation, year_month]
            if type_all != '1':
                sql += "AND ref_type IN ({}) ".format(', '.join(types_placeholder))
                params += types
            sql += "ORDER BY journal_date DESC"
            cursor.execute(sql, params)
            journal = cursor.fetchall()
            cursor.close()
        return journal

    def __fetch_sum_months(self, corporation: int, year_month: int, types: [], types_placeholder: [], type_all: str) -> []:
        journal = []
        if len(types) > 0 or type_all == '1':
            cursor = self.__db.cursor(dictionary=True)
            sql = "SELECT journal_year_month, " \
                  "    SUM(CASE WHEN amount >= 0 THEN amount ELSE 0 END) AS amount_in, " \
                  "    SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END) AS amount_out " \
                  "FROM wallet_journal " \
                  "WHERE corporation_id = %s AND journal_year_month >= %s AND journal_year_month < %s AND division = 1 AND ref_type NOT IN ('daily_goal_payouts', 'corporation_account_withdrawal', 'freelance_jobs_reward') "
            params = [corporation, year_month, year_month+100]
            if type_all != '1':
                sql += "AND ref_type IN ({}) ".format(', '.join(types_placeholder))
                params += types
            sql += "GROUP BY journal_year_month " \
                   "ORDER BY journal_year_month DESC"
            cursor.execute(sql, params)
            journal = cursor.fetchall()
            cursor.close()
        return journal

    def __fetch_sum_corporations(self, year_month: int, types: [], types_placeholder: [], type_all: str) -> []:
        journal = []
        if len(types) > 0 or type_all == '1':
            cursor = self.__db.cursor(dictionary=True)
            sql = "SELECT w.corporation_id, c.corporation_name, " \
                  "    SUM(CASE WHEN w.amount >= 0 THEN w.amount ELSE 0 END) AS amount_in, " \
                  "    SUM(CASE WHEN w.amount < 0 THEN w.amount ELSE 0 END) AS amount_out " \
                  "FROM wallet_journal w " \
                  "LEFT JOIN corporations c ON w.corporation_id = c.id " \
                  "WHERE w.journal_year_month = %s AND w.division = 1 AND w.ref_type NOT IN ('daily_goal_payouts', 'corporation_account_withdrawal', 'freelance_jobs_reward') "
            params = [year_month]
            if type_all != '1':
                sql += "AND w.ref_type IN ({}) ".format(', '.join(types_placeholder))
                params += types
            sql += "GROUP BY w.corporation_id, c.corporation_name " \
                   "ORDER BY c.corporation_name"
            cursor.execute(sql, params)
            journal = cursor.fetchall()
            cursor.close()
        return journal

    @staticmethod
    def __create_csv(params: {}, journal: []) -> str:
        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_NONNUMERIC)

        if params['mode'] == 'details':
            writer.writerow(['ref_type', 'journal_date', 'description', 'amount in', 'amount out', 'reason'])
            for entry in journal:
                writer.writerow([entry['ref_type'], entry['journal_date'], entry['description'], entry['amount_in'],
                                 entry['amount_out'], entry['reason']])

        if params['mode'] == 'sum_months':
            writer.writerow(['year month', 'amount in', 'amount out'])
            for entry in journal:
                writer.writerow([entry['journal_year_month'], entry['amount_in'], entry['amount_out']])

        if params['mode'] == 'sum_corporations':
            writer.writerow(['corporation', 'amount in', 'amount out'])
            for entry in journal:
                writer.writerow([entry['corporation_name'], entry['amount_in'], entry['amount_out']])

        return output.getvalue()

    @staticmethod
    def __int(s) -> int:
        try:
            integer = int(s)
            return integer
        except (ValueError, TypeError):
            return 0
