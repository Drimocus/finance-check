# install required packages
Make sure the machine has the required python packages.
- `python -m pip install -r check_taxes_requirements.txt`
- I assumed the same python version (3.12.10)
- I assumed you already use the packages in `existing_requirements_questionmark.txt`

# update your database schema
The sql commands to make these changes are added at the bottom of the `schema.sql` file.
- add `division` column to `wallet_journal`
- add `is_alt_corp`, `corporation_ceo_id`, `corporation_owner_id` columns to `corporations`
- add the `tax_records` table

These schema changes are backwards compatible, no old data is removed / altered. As usual, make a backup of your database first, though there should be no risk.

- corporation_ceo_id is automated, you do NOT have to add this by hand!
- SET the is_alt_corp value for (some test) corps, this one is not optional
- SET corporation_owner_id value for (some test) corps if you want the script to be able to evemail someone other than the ceo (optional).

# part 1 - does the tax calculation work (no mails yet)
## check and update `config/tax_check_config.json`
- check the base tax amounts, and taxed ref types
- check the help contacts / tax receiving corp.
- check the excluded_corps
- leave the `evemail_..._corps` settings on `false` for now, we do a local test first, rest of config comes later.

## run script manually with evemail_..._corps: false in config
- SET `is_alt_corp` and `corporation_owner_id` values for some test corps if not done yet.
- When you run the script, it will check taxes of last month for all corps in the `corporations` table. The result for each corp will be printed, and added to the `tax_records` table.
- `python console/check_taxes.py` to run the script, assuming you are in the root folder. Check that the printed results are as expected. Check that the script still works with a big amount of corps (ccp rate limits)
- you can mess with the `current_date` inside `check_taxes.py - line 14` to try different or successive months (`+/- timedelta(days=??)`), just remember to change it back to normal after. Note: atm tax check uses the most recent balance to calculate the next one, so only successive new months work properly. dont expect the balances to auto-fix eachother if you try to check months in a weird order, he ain't that smart yet boss. This also means, in case of future accidents -> just fix the most recent balance date in tax_records, if that is correct the next report should be fine again as well.

If all the above works as expected, delete all entries from the tax_records table to reset it for the real thing. You made a database backup earlier right ? just in case you type the wrong thing...

# part 2 - evemails
## register with ccp to get a client_id to be able to send eve-mail
Log in, and fill out the form on `https://developers.eveonline.com/applications` -> Create Application:
- Name: whatever you like
- Dsicription: whatever you like
- Callback URL: `http://localhost:8282/callback/`
- Enabled Scopes:
  - [x] publicData
  - [-] esi-mail
    - [x] esi-mail.send_mail.v1

## check and update `config/tax_check_config.json`
- add your client_id
- set the desired `evemail_..._corps` settings to `true`
- check the localhost port matches the one for the Callback URL above
- if there are corps you do not want checked / mailed, make sure they are in `excluded_corporations`
- make sure that the `is_alt_corp` for all active, not excluded, corporations in the corporations table is correct, or you will be sending embarrasing wrong emails.
- make sure the `corporation_owner_id` is set for corps where you want to  evemail another character that isn't the ceo.

## run the script manually to see if it works
- `python console/check_taxes.py`
- it will ask you to add an api key for the mail sender character. You can choose any character, could make a fun new 'brave-tax-agent' name, does not even have to be in Brave.
- after adding the key, it should begin listing corp tax reports like before, but it will also be evemailing.

## set up cronjob for the main script
If it all works as expected, the only last thing to do is setting up a cronjob for `python console/check_taxes.py`.

Ideally we want tax mails to go out early at the start of each month, something like `0 0 2 * *` for the start of 2nd day of each month.
BUT this requries that the corp wallet data has already been updated in your DB. 

Your old readme mentions that fetch-wallets.py is already a cronjob, maybe check that and modify it to also run on `0 0 1 * *`. fetch wallet needs to run more often than that, because ccp only keeps data for 30 days. but for our tax checking it would be nice if it ALSO runs at the start of each month (and finishes before midnight of that day).

## what might break: rate limits
Understandably, I have not yet tested this with 100+ corps
- with all corps we hit the corp-wallet rate limits (300/15m) pulling all divisiosn

I assume the brave API relay already has caching and rate limit handling and our updated fetch-wallets script just takes a little longer ? If not we need to add a small wait inbetween requests when we're close to the limit.

- we should be fine on the send-mail rate limit (600/15m) and the endpoints for corp names & ceo's.