"""
    make sure mailer character token is still ok
    make pretty eve mail message
    send pretty evemail message
"""
import json
import requests

def prefix_str(name_str: str, min_len) -> str:
    """for nice values alignment""" 
    while len(name_str) < min_len:
        name_str = " " + name_str
    return name_str

def prepare_tax_mail(tax_record: dict, config: dict) -> tuple[str, list[dict], str]:
    """"make body, recipients, mail subject for evemail"""
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
        body += f"TLDR: send {brave_tax_balance*-1:,} to {tax_receiving_corp}\n\n"
    else:
        body += f"TLDR: you already paid us {brave_tax_balance:,} too much, see you next month\n\n"
    body += (
        "---- Tax Report ----\n"
        f"Date:              {prefix_str(f"{tax_month_date.strftime("%B")} - {tax_month_date.strftime("%Y")}", 18)}\n"
        f"Corporation Name:{prefix_str(corporation_name, 20)}\n"
        f"Corporation ID:  {prefix_str(str(corporation_id), 20)}\n"
        f"Tax Income:      {prefix_str(f"{taxable_income:,}", 20)} ISK\n"
        f"Corp Tax:        {prefix_str(f"{corp_tax_amount:,}", 20)} ISK\n"
        f"Brave Tax:       {prefix_str(f"{brave_tax_amount:,}", 20)} ISK\n"
        f"Brave Tax Payments:{prefix_str(f"{brave_tax_payments:,}", 18)} ISK\n"
        f"\n"
        f"Previous Brave Tax Balance:{prefix_str(f"{previous_balance:,}", 16)} ISK\n"
        f"Current Brave Tax Balance:" + prefix_str(f"{brave_tax_balance:,}", 15) + " ISK\n"
        "--------------------\n"
        "\n"
        "Explanation:\n"
        "Tax Income: the total amount your corporation wallet gained from taxes in this month.\n"
    )
    if is_alt_corp:
        body += (
            "Corp Tax: the amount of your alt corp's monthly tax income that should go to your main corp (50%).\n"
        )
    else:
        body += (
            "Corp Tax: the amount of your corp's monthly tax income that your corp can keep for itself (50%).\n"
        )
    body += (
        f"Brave Tax: the amount of your corp's monthly tax income that should go to {tax_receiving_corp} (50%)"
    )
    if exempt_amount > 0:
        body += f", your first {exempt_amount:,} ISK of tax income was free from brave taxes."
    if base_tax > 0:
        body += f" This includes the {base_tax:,} ISK base tax for {('alt' if is_alt_corp else 'main')} corps.\n"
    else:
        body += ".\n"
    body += (
        "Brave Tax Payments: the amount of ISK your corporation has transferred to Brave United Holding this month.\n"
        "\n"
        "Previous Brave Tax Balance: Your corp's tax record before this month.\n"
        "Current Brave Tax Balance: Your corp's current tax record with Brave. If it is negative, this is the amount of tax you still need to pay. If this balance is positive, you overpaid before and do not currently need to pay tax.\n"
    )

    return body, recipients, mail_subject

def post_tax_mail(
    access_token: str,
    evemail_endpoint: str,
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

    # corp / alliance recipient = only your own, only if enabled by ingame roles
    mail_info = {
        "approved_cost": approved_cost,
        "body": body,
        "recipients": recipients,
        "subject": subject
    }

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    res = requests.post(
        url = evemail_endpoint,
        data=json.dumps(mail_info),
        headers=headers,
        timeout=15
    )
    if res.status_code != 201:
        print(
            f'Failed to send mail: Status Code: {res.status_code},',
            f'Reason: {res.reason}, Body: {res.text}'
        )
    else:
        print(f"sent mail with subject '{subject}' to recipients {recipients}")
