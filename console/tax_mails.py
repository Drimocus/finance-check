"""
    make sure mailer character token is still ok
    make pretty eve mail message
    send pretty evemail message
"""

import common as common
from common import write_json_file
import json
from jose import ExpiredSignatureError
from authentication import refresh_token, validate_eve_jwt, SEND_MAIL_SCOPE
from datetime import datetime
from requests.exceptions import HTTPError
import requests

def token_exp_date(token_fields):
    return datetime.fromtimestamp(token_fields["exp"])

def check_mailer_token():
    client_id = common.config["client_id"]
    mailer_char_key = common.config["mailer_character_api_key"]
    access_exc_str = f"{mailer_char_key["access_token"][:10]}...{mailer_char_key["access_token"][-10:]} (expired -failed to refresh)"
    refresh_exc_str = f"{mailer_char_key["refresh_token"][:10]}...{mailer_char_key["refresh_token"][-10:]} (failed to refresh)"
    try:
        mailer_token_fields = validate_eve_jwt(mailer_char_key["access_token"])
        print(f"Mailer character token valid, name: {mailer_token_fields["name"]}, expires at", token_exp_date(mailer_token_fields), "\n")
    except ExpiredSignatureError as e:
        try:
            mailer_char_key = refresh_token(mailer_char_key["refresh_token"], client_id)
        except HTTPError as e:
            print(f"Failed to refresh token for mailer character key {access_exc_str}, mailing disabled\n")
            common.config["mailer_character_api_key"] = {
                "access_token": access_exc_str,
                "refresh_token": refresh_exc_str,
            }
            common.config["evemail_tax_reports"] = False
            return
        if mailer_char_key is None:
            print(f"Failed to refresh token for mailer character key {access_exc_str}, mailing disabled\n")
            common.config["mailer_character_api_key"] = {
                "access_token": access_exc_str,
                "refresh_token": refresh_exc_str,
            }
            common.config["evemail_tax_reports"] = False
            return
        mailer_token_fields = validate_eve_jwt(mailer_char_key["access_token"])
        common.config["mailer_character_api_key"] = {
            "access_token": mailer_char_key["access_token"],
            "refresh_token": mailer_char_key["refresh_token"],
        }
        print(f"Mailer character token refreshed, name: {mailer_token_fields["name"]}, expires at", token_exp_date(mailer_token_fields), "\n")
    
    write_json_file(common.config, "config.json")

    scope = mailer_token_fields.get("scp", [])
    if SEND_MAIL_SCOPE not in scope:
        print(f"Mailer character token does not have required scope '{SEND_MAIL_SCOPE}', mailing disabled\n")
        common.config["evemail_tax_reports"] = False

def prefix_str(name_str: str, min_len) -> str:
    while len(name_str) < min_len:
        name_str = " " + name_str
    return name_str

def send_tax_mail(tax_record: dict) -> None:
    tax_month_date = tax_record["tax_month_date"]
    corporation_name =  tax_record["corporation_name"]
    corporation_id = tax_record["corporation_id"]
    taxable_income = tax_record["taxable_income"]
    corp_tax_amount = tax_record["corp_tax_amount"]
    brave_tax_amount = tax_record["brave_tax_amount"]
    brave_tax_payments = tax_record["brave_tax_payments"]
    brave_tax_balance = tax_record["brave_tax_balance"]
    corporation_ceo_id = tax_record["corporation_ceo_id"]
    corporation_owner_id = tax_record["corporation_owner_id"]
    is_alt_corp = tax_record["is_alt_corp"]
    
    if is_alt_corp:
        tax_receiving_corp = common.config['alt_corps_tax_receiving_corp']
        tax_contact = common.config['alt_corps_tax_contact']
        tax_help_channel = common.config['alt_corps_tax_help_channel']
        base_tax = common.config['alt_corps_base_tax']
    else:
        tax_receiving_corp = common.config['main_corps_tax_receiving_corp']
        tax_contact = common.config['main_corps_tax_contact']
        tax_help_channel = common.config['main_corps_tax_help_channel']
        base_tax = common.config['main_corps_base_tax']
    
    previous_balance = brave_tax_balance + brave_tax_amount - brave_tax_payments
    mail_subject = f"{tax_month_date.strftime("%B")} {tax_month_date.strftime("%Y")} Tax Report"
    recipients = [
        {
            "recipient_id": corporation_ceo_id,
            "recipient_type": "character"
        }
    ]

    if corporation_owner_id is not None and corporation_owner_id != 0 and corporation_owner_id != corporation_ceo_id:
        recipients.append({
            "recipient_id": corporation_owner_id,
            "recipient_type": "character"
        })

    body = (
        f"This report is automatically generated, replies to this character will not be read.\n"
        f"If you have questions, ask {tax_contact} on slack or post in {tax_help_channel}.\n"
        f"\n"
    )
    if brave_tax_balance < 0:
        body += f"TLDR: send {brave_tax_balance*-1:,.2f} to {tax_receiving_corp}\n\n"
    else:
        body += f"TLDR: you already paid us {brave_tax_balance:,.2f} too much, see you next month\n\n"
    body += (
        f"---- Tax Report ----\n"
        f"Date:              " + prefix_str(f"{tax_month_date.strftime("%B")} - {tax_month_date.strftime("%Y")}", 20) + "\n"
        f"Corporation Name:  {prefix_str(corporation_name, 20)}\n"
        f"Corporation ID:    {prefix_str(str(corporation_id), 20)}\n"
        f"Tax Income:        " + prefix_str(f"{taxable_income:,.2f}", 20) + " ISK\n"
        f"Corp Tax:          " + prefix_str(f"{corp_tax_amount:,.2f}", 20) + " ISK\n"
        f"Brave Tax:         " + prefix_str(f"{brave_tax_amount:,.2f}", 20) + " ISK\n"
        f"Brave Tax Payments:" + prefix_str(f"{brave_tax_payments:,.2f}", 20) + " ISK\n"
        f"\n"
        f"Previous Brave Tax Balance:" + prefix_str(f"{previous_balance:,.2f}", 17) + " ISK\n"
        f"Current Brave Tax Balance: " + prefix_str(f"{brave_tax_balance:,.2f}", 17) + " ISK\n"
        f"--------------------\n"
        f"\n"
        f"Explanation:\n"
        f"Tax Income: the total amount your corporation wallet gained from taxes in this month.\n"
    )
    if is_alt_corp:
        body += (
            f"Corp Tax: the amount of your alt corp's monthly tax income that should go to your main corp (50%).\n"
        )
    else:
        body += (
            f"Corp Tax: the amount of your corp's monthly tax income that your corp can keep for itself (50%).\n"
        )
    body += (
        f"Brave Tax: the amount of your corp's monthly tax income that should go to {tax_receiving_corp} (50%)"
    )
    if base_tax > 0:
        body += f", also includes the {base_tax:,.2f} ISK base tax for {('alt' if is_alt_corp else 'main')} corps.\n"
    else:
        body += ".\n"
    body += (
        f"Brave Tax Payments: the amount of ISK your corporation has transferred to Brave United Holding this month.\n"
        f"\n"
        f"Previous Brave Tax Balance: Your corp's tax record before this month.\n"
        f"Current Brave Tax Balance: Your corp's current tax record with Brave. If it is negative, this is the amount of tax you still need to pay. If this balance is positive, you overpaid before and do not currently need to pay tax.\n"
    )
    post_tax_mail(
        common.config["mailer_character_api_key"]["access_token"],
        recipients,
        body,
        mail_subject,
    )

def post_tax_mail(
    access_token: str, 
    recipients: list[dict], 
    body: str, 
    subject: str, 
    approved_cost: int = 0
):
    """
    recipients: [
        "recipient_id": 0,
        "recipient_type": "alliance","character","corporation","mailing_list"
    ]
    """
    mailer_token_fields = validate_eve_jwt(access_token)
    mailer_char_id = mailer_token_fields["sub"]
    ccp_mail_endpoint = f"https://esi.evetech.net/characters/{mailer_char_id}/mail"
    
    # corp / alliance recipient = only your own, only if enabled by ingame roles
    mail_info = {
        "approved_cost": approved_cost,
        "body": body,
        "recipients": recipients,
        "subject": subject
    }

    headers = {
        "Authorization": "Bearer {}".format(access_token)
    }

    res = requests.post(
        url = ccp_mail_endpoint,
        data=json.dumps(mail_info),
        headers=headers
    )
    if res.status_code != 200:
        print('Failed to send mail: Status Code: {}, Reason: {}, Body: {}'
              .format(res.status_code, res.reason, res.text))
    else:
        print("sent mail with subject '{}' to recipients {}".format(subject, recipients))