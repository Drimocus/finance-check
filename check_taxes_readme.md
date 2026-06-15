# Setup Tasks

## no extra python package requirements
- original pipfile already provides what is needed, asumed python 3.12.10

## Neucore Settings
- Administration > EVE Logins > Add a new eve login (finanace-mails), give it the `esi-mail.send_mail.v1` scope. Add a token to it by clicking the login URL and signing in with the character you want to send emails from.
- Administration > Finance > EVE Logins > Add the new finance-mails login created in the previous step to the existing finance application.

## Slack App
The person in charge of slack tax communication should make a new slack app.

- https://api.slack.com/apps -> Create New App -> From scratch
- `App Name` : tax-reports
- `Pick a workspace to develop your app in` : Brave Collective
  - unless you are an admin, you will see an approval is necessary message
- Select `OAuth & permissions` on the left menu, in the Features section
- Under `User Token Scopes`, add the following scopes with the `Add an Oath Scope` Button:
  - `chat:write`
  - `users:read`
  - `channels:read`
  - `groups:read`
- Scroll back up, but still in `OAuth & Permissions`, click `Install to Brave Collective` -> `Allow`
  - If you needed approval, click `Request to Workspace Install`, add a description if asked.
  - wait for approval, then refresh the page and click `Install to Brave Collective` -> `Allow`
- Copy the `User OAuth Token`, it usually starts with `xoxp-`, and set it as FINANCE_SLACK_USER_OAUTH_TOKEN environment variable.

The users and chat scopes are needed to be able to find the slack account of corporation owners, and send them tax reports. The channels and groups scopes are necessay to be able to find the generic tax notification / help channels.

We use the User Based application instead of a Bot messages based version. This is nice because it lets you, the tax contact, log into slack and see the messages sent as if they were your own. If people reply with questions you can answer them.

## add additional environment variables
- `FINANCE_MAILS_EVE_LOGIN` - the new neucore eve login with the send-mail scope. 
- `FINANCE_MAILS_CHAR_NAME` - the character that should send the evemails, the eve login for finance mails should have a token for this character, and the finance application should have access to the eve-login.
- `FINANCE_SLACK_USER_OAUTH_TOKEN` - the token for the slack app to send tax reports and notifications on the brave slack.

That is assuming we still have the existing ones from finance : `[DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_DATABASE, API_BASE_URL, API_KEY, API_EVE_LOGIN, LOGIN_CHARACTERS, EVE_APP_CALLBACK, EVE_APP_SECRET, EVE_APP_ID, SECRET_KEY, CHECK_ALLIANCES, CHECK_CORPORATIONS]`

## update the database schema of the finance-check DB
With finance-check DB we mean the existing one for finance with wallet_journal and corporations tables. The sql commands to make these changes are added to the `schema.sql` file. Suggest creating table backups first.
- we add division id to wallet_journal and now fetch all wallet divisions of corps.
- we add more fields to corporations to reduce api calls during updates and webpage loading.
- we add a tax_records table, this makes it easy to find past records and speeds up constructing new records. Potentially allows wallet journal data archiving, as only wallet data from the most recent month is needed to make a new balance calculation with known previous tax records.

## start this flask app as usual like the old finance and test it
- check the tax settings at the bottom of the tokens page
- test the token configuration / info field editing for corporations
- use the test buttons to check if the wallet, tax records and evemails work
  - evemail buttons send real mails, only use if necessary for debugging
- optionally delete the test buttons section from the html if convinced.
- get someone to actually add the corp info on the tokens page (owner contact, corp type, taxable, old balance)

## set up cronjobs
- remove the old `python console/fetch-wallets.py` cron task
- replace it with a new task for `python web/wallets.py`, would liket this to run at `0 0 1/10 * *`, need at least 2 per month to cover the limit of 30 days that esi wallet data has. This will update wallet journal for all corps as before, but now for all divisions. It will take longer, rate limits should be respected. Data produced should not be substantially more, most records are in divison 1. This covers mistakes of people sending tax payments from the wrong division, worth saving the trouble of talking to people and manually fixing the records.
- add a new task for `python web/tax_records.py`, would like this to run at `0 0 2 * *`, that should be plenty of time for the wallet update to have finished, which we need for tax records. This constructs tax records for each corp for the previous month, and sends it to each corp's ceo & owner if known.

## what might break: rate limits / neucore
I have tested the evemail and wallet updates, they do have a very naive sleep rate limiter to stay under 5 mails and 10 wallet requests per minute.
With all current 216 corps this means mailing likely takes ~40 minutes and a full wallets update up to 3 hours. If this still runs into issues it might need to be managed further.
