see also check_taxes_plan.txt...

# current setup tasks

## no extra python package requirements
- original pipfile already provides what is needed, asumed python 3.12.10

## add additional environment variables
- `MAILER_NEUCORE_KEY` - new neucore app api key with send_mail scope, or old one of finance-check if it has it already.
- `MAILER_NEUCORE_LOGIN_NAME` - new neucore app name or re-use old one of finance if send_mail scope is already allowed.
- `MAILER_CHARACTER_ID` - character that should send the evemails, we assume neucore already has a valid key for this character and is able to find it if we provide the correct header / datasource params

  could make a new character for this with a brave-tax-agent or other funny name... titan-chicken fund collector? but should probably be trustworthy and in official corp.

Assuming we still have the existing ones: DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_DATABASE, API_BASE_URL

## update the database schema of the finance-check DB (not neucore DB)
The sql commands to make these changes are added at the bottom of the `schema.sql` file.
- add `division` column to `wallet_journal`
- add `is_alt_corp`, `corporation_ceo_id`, `corporation_owner_id` columns to `corporations`
- add the `tax_records` table

The schema and fetchwallet / webpage code changes are backwards compatible, no old data is removed / altered. As usual, make a backup of your database first, though there should be no risk.

- is_alt_corp: not optional, and needs manual setup, don't think we have a way to look it up currently? need to know which is which for proper tax automation, should be useful for other things as well in future.
- corporation_owner_id: optional, necessary if we want the script to be able to evemail someone other than the ceo.
- corporation_ceo_id: automated lookup and addition, it's stored to save future lookups / potential re-use by others.

# test part 1 - tax calculation only,no evemails yet
## check and update `config/tax_check_config.json`
- check the base tax & exempt income amounts, and taxed ref types
- check the help contacts / tax receiving corp.
- leave the `evemail_..._corps` settings on `false` for now, do a local test first
- optional: set the table_name used for corporations & wallet_journal, if we want to test with a copy / different data (less corps).

## run script manually with evemail_..._corps: false in config
- SET `is_alt_corp` and `corporation_owner_id` values for some test corps if not done yet.
- When you run the script, it will check taxes of last month for all corps in the `corporations` table. The result for each corp will be printed, and added to the `tax_records` table.
- `python console/check_taxes.py` to run the script, assuming you are in the root folder. Check that the printed results are as expected. Check that the script still works with a large amount of corps (ccp rate limits)
- you can mess with the `current_date` inside `check_taxes.py - line 13` to try different or successive months (`+/- timedelta(days=??)`), just remember to change it back to normal after. Note: atm tax check uses the most recent balance to calculate the next one, so only successive new months work properly. dont expect the balances to auto-fix eachother if you try to check months in a weird order, he ain't that smart yet boss. This also means, in case of future accidents -> we need to fix the most recent balance date in tax_records. But that would be annoying, so might need a web edit form, or preferred automate out mistake if possible.

If all the above works as expected, delete all entries from the tax_records table to reset it for the real thing. You made a database backup earlier right ? just in case you type the wrong thing...

# part 2 - evemails
We will be evemailing, mistakes lead to embarrasing evemail explanations for the contact person.

## update `config/tax_check_config.json`
- set the desired `evemail_..._corps` settings to `true`

  ALL MATCHING CORPS IN THE CORPORATIONS DATABASE WILL BE CHECKED AND EVEMAILED
- check if still using desired table_names for corp & wallet info
- if there are corps you do not want to be checked & mailed, make sure they are in `excluded_corporations`
- make sure that the `is_alt_corp` is correct for all active, not excluded, corporations in the corporations table, or you might be sending embarrasing wrong emails.
- make sure the `corporation_owner_id` is set for corps where you want to  evemail another character that isn't the ceo.

## run the script manually to see if it works
- `python console/check_taxes.py`
- after adding the key, it should begin listing corp tax reports like before, but it will also be evemailing. Check ingame from sender / receiver.

# final permanent setup
## recheck config & database correct
- table names in config if altered for testing
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