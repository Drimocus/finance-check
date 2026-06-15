'''
    Using the send automated messages as user form of slack app.
    This is nicer than the bot variant, selected user can log in and 
    see the conversations and respond if necessary.

    slack scopes
        # to send tax reports direct messages to corp owners
        chat:write      - able to write message as user
        users:read      - able to see who is in workspace

        # to send tax notifications in the (alt) corp owners channel
        channels:read   - able to see public channels info
        groups:read     - able to see private channel info of user
'''
import time
import os
import logging
import requests

env_vars = {
    'FINANCE_SLACK_USER_OAUTH_TOKEN': os.getenv('FINANCE_SLACK_USER_OAUTH_TOKEN', '')
}

def prefix_str(name_str: str, min_len) -> str:
    """for nice values alignment""" 
    while len(name_str) < min_len:
        name_str = " " + name_str
    return name_str

class SlackMessages:
    '''use the slack api to send tax reports to owners'''

    def __init__(self, logger = None):
        if logger is None:
            logger = logging.getLogger('tax_records')
            logging.basicConfig(
                filename='tax_records.log',
                encoding='utf-8',
                level=os.getenv('UWSGI_LOG_LEVEL', 'ERROR').upper()
            )
        self.__logger = logger

        if env_vars['FINANCE_SLACK_USER_OAUTH_TOKEN'] == '':
            self.__logger.error(
                'slack messages : FINANCE_SLACK_USER_OAUTH_TOKEN environment variable missing.'
            )
            raise ValueError('FINANCE_SLACK_USER_OAUTH_TOKEN undefined')

        self.__auth_header = {
            'Authorization': 'Bearer ' + env_vars['FINANCE_SLACK_USER_OAUTH_TOKEN']
        }

        self.session = requests.session()
        self.__slack = self.__auth_check_user()

        channels = self.get_channels()
        self.__channels_by_name = {}
        for channel in channels:
            self.__channels_by_name[channel['name']] = channel

        members = self.get_members()
        self.__members_by_real_name = {}
        for memb in members:
            self.__members_by_real_name[memb['real_name']] = memb

    def __auth_check_user(self):
        res = self.session.get(
            url = 'https://slack.com/api/auth.test',
            headers = self.__auth_header,
            timeout=15
        )
        if res.status_code == 429:
            self.__logger.info('__auth_check_user rate-limited, retrying in 15')
            time.sleep(15)
            return self.__auth_check_user()
        if res.status_code != 200:
            self.__logger.error(
                '__auth_check_user: %s, %s, %s',
                res.status_code, res.headers, res.content
            )
        res = res.json()
        if not res['ok']:
            self.__logger.error(
                '__auth_check_user invalid token: %s',
                env_vars['FINANCE_SLACK_USER_OAUTH_TOKEN']
            )
        if res.get('bot_id', None) is not None:
            self.__logger.error(
                '__auth_check_user expected user token, but got bot token: %s',
                env_vars['FINANCE_SLACK_USER_OAUTH_TOKEN']
            )
        return res

    def get_channels(self):
        '''get current slack workspace members'''
        res = self.session.get(
            url = 'https://slack.com/api/conversations.list',
            headers = self.__auth_header,
            params = {
                'types': 'public_channel,private_channel'
            },
            timeout=15
        )
        if res.status_code == 429:
            self.__logger.info('get_members rate-limited, retrying in 15')
            time.sleep(15)
            return self.get_members()
        if res.status_code != 200:
            self.__logger.error(
                'get_members: %s, %s, %s',
                res.status_code, res.headers, res.content
            )
        cdata = res.json()
        return cdata['channels']

    def get_members(self):
        '''get current slack workspace members'''
        res = self.session.get(
            url = 'https://slack.com/api/users.list',
            headers = self.__auth_header,
            timeout=15
        )
        if res.status_code == 429:
            self.__logger.info('get_members rate-limited, retrying in 15')
            time.sleep(15)
            return self.get_members()
        if res.status_code != 200:
            self.__logger.error(
                'get_members: %s, %s, %s',
                res.status_code, res.headers, res.content
            )
        mdata = res.json()
        return mdata['members']

    def get_member_by_real_name(self, member_name):
        '''find a slack member by their real_name (chosen slack display name)'''
        return self.__members_by_real_name.get(member_name, None)
    def get_channel_by_name(self, channel_name):
        '''find a slack member by their real_name (chosen slack display name)'''
        return self.__channels_by_name.get(channel_name, None)

    def send_message(self, channel_id, message):
        '''send a slack dm to a slack member using their id'''
        res = self.session.post(
            url = 'https://slack.com/api/chat.postMessage',
            headers = self.__auth_header,
            params = {
                'channel': channel_id,
                'text': message,
            },
            timeout=15
        )
        if res.status_code == 429:
            self.__logger.info('send_message rate-limited, retrying in 15')
            time.sleep(15)
            return self.send_message(channel_id, message)
        if res.status_code != 200:
            self.__logger.error(
                'dm_member : %s, %s, %s',
                res.status_code, res.headers, res.content
            )
            return False
        return True

    def format_slack_report(self, tax_record, config):
        """"make body, recipients, mail subject for evemail"""
        tax_month_date = tax_record["tax_month_date"]
        corporation_name =  tax_record["corporation_name"]
        corporation_id = tax_record["id"]
        taxable_income = tax_record["taxable_income"]
        corp_tax_amount = tax_record["corp_tax_amount"]
        br_tax_amount = tax_record["brave_tax_amount"]
        br_tax_payments = tax_record["brave_tax_payments"]
        br_tax_balance = tax_record["brave_tax_balance"]
        is_alt_corp = tax_record["is_alt_corp"]

        if is_alt_corp:
            tax_receiving_corp = config['alt_corps_tax_receiving_corp']
            tax_contact = config['alt_corps_tax_contact']
            tax_help_channel = config['alt_corps_tax_help_channel']
            base_tax = config['alt_corps_base_tax']
            exempt_amount = config['alt_corps_exempt_income']
        else:
            tax_receiving_corp = config['main_corps_tax_receiving_corp']
            tax_contact = config['main_corps_tax_contact']
            tax_help_channel = config['main_corps_tax_help_channel']
            base_tax = config['main_corps_base_tax']
            exempt_amount = config['main_corps_exempt_income']

        tax_channel_slack = self.get_channel_by_name(tax_help_channel)
        if tax_channel_slack is not None:
            tax_help_channel_str = f'<#{tax_channel_slack['id']}>'
        else:
            tax_help_channel_str = f'{tax_help_channel} (unknown slack channel)'

        tax_contact_slack = self.get_member_by_real_name(tax_contact)
        if tax_contact_slack is not None:
            if self.__slack['user_id'] == tax_contact_slack['id']:
                tax_contact_str = 'I am also your tax contact here, if there are any questions you can ask them'
            else:
                tax_contact_str = f'<{self.__slack['url']}/team/{tax_contact_slack['id']}|{tax_contact}> is your tax contact here if there are any questions you can ask them'
        else:
            tax_contact_str = f'{tax_contact} (unknown slack user) is your tax contact if there are any questions you can ask them'

        previous_balance = br_tax_balance + br_tax_amount - br_tax_payments
        month_str = f"{tax_month_date.strftime("%B")}"
        year_str = f"{tax_month_date.strftime("%Y")}"

        body = (
            f"{tax_month_date.strftime("%B")} {tax_month_date.strftime("%Y")} Tax Report\n"
            "\n"
            f"This tax report message was automatically generated. \n"
            f"{tax_contact_str}, or post in {tax_help_channel_str}.\n"
            "\n"
        )
        if br_tax_balance < 0:
            body += f"TLDR: send {br_tax_balance*-1:,} to {tax_receiving_corp}\n\n"
        else:
            body += f"TLDR: you already paid us {br_tax_balance:,} too much, see you next month\n\n"
        exempt_amount = min(taxable_income - corp_tax_amount, exempt_amount)
        brave_tax_str = prefix_str(f'{taxable_income - corp_tax_amount:,} ISK', 23)
        exempt_str = prefix_str(f"({exempt_amount:,} ISK)", 24)
        brave_tax_line = (
            f"Brave Tax Income:{brave_tax_str}\n"
            f"                 {exempt_str} Exempt\n"
        )
        if base_tax > 0:
            base_tax_string = prefix_str(f"{base_tax:,}", 21)
            base_tax_line = f"Brave Base Tax:{base_tax_string} ISK\n"
        else:
            base_tax_line = ""
        body += (
            "```"
            "-------- Tax Report --------\n"
            f"Date:            {prefix_str(f"{month_str} - {year_str}", 19)}\n"
            f"Corporation Name:{prefix_str(corporation_name, 19)}\n"
            f"Corporation ID:  {prefix_str(str(corporation_id), 19)}\n"
            f"Tax Income:      {prefix_str(f"{taxable_income:,}", 19)} ISK\n"
            f"Corp Tax Income: {prefix_str(f"{corp_tax_amount:,}", 19)} ISK\n"
            f"{brave_tax_line}"
            f"{base_tax_line}"
            f"\n"
            f"Brave Tax Amount:  {prefix_str(f"{br_tax_amount:,}", 17)} ISK\n"
            f"Brave Tax Payments:{prefix_str(f"{br_tax_payments:,}", 17)} ISK\n"
            f"\n"
            f"Previous Brave Tax Balance:{prefix_str(f"{previous_balance:,}", 15)} ISK\n"
            f"Current Brave Tax Balance:" + prefix_str(f"{br_tax_balance:,}", 16) + " ISK\n"
            "----------------------------\n"
            "```"
            "\n"
            "Explanation:\n"
            "`Tax Income`: The total amount your corporation wallet gained from taxes in this month.\n"
        )
        if is_alt_corp:
            body += (
                "`Corp Tax Income`: The amount of your alt corp's monthly tax income that should go to your main corp (50%).\n"
            )
        else:
            body += (
                "`Corp Tax Income`: The amount of your corp's monthly tax income that your corp can keep for itself (50%).\n"
            )
        body += (
            f"`Brave Tax Income`: The amount of your corp's monthly tax income that should go to {tax_receiving_corp} (50%)"
        )
        if exempt_amount > 0:
            body += f", your first {exempt_amount:,} ISK of tax income was free from brave taxes.\n"
        if base_tax > 0:
            body += f"`Brave Base Tax`: The {base_tax:,} ISK base tax for {('alt' if is_alt_corp else 'main')} corps, regardless of income.\n"
        else:
            body += ".\n"
        body += (
            "`Brave Tax Amount`: The resulting amount you owe Brave for this month.\n"
            "`Brave Tax Payments`: The amount of ISK your corporation has transferred to Brave United Holding this month.\n"
            "\n"
            "`Previous Brave Tax Balance`: Your corp's tax record before this month.\n"
            "`Current Brave Tax Balance`: Your corp's current tax record with Brave. If it is negative, this is the amount of tax you still need to pay. If this balance is positive, you overpaid before and do not currently need to pay tax.\n"
        )

        return body
