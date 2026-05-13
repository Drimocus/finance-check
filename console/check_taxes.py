# called by cronjob, 002** : python console/check_taxes.py

import os
import mysql.connector

# mock env
os.environ['API_BASE_URL'] = ''
os.environ['API_KEY'] = ''
os.environ['API_EVE_LOGIN'] = ''
os.environ['DB_HOST'] = ''
os.environ['DB_PORT'] = ''
os.environ['DB_USER'] = ''
os.environ['DB_PASSWORD'] = ''
os.environ['DB_DATABASE'] = ''

env_vars = {
    'api_base_url' : os.getenv('API_BASE_URL'),
    'api_key' : os.getenv('API_KEY'),
    # user info for brave api relay (datasource=char_id:api_login_name)
    'api_login_name' : os.getenv('API_EVE_LOGIN'),
    'db_host' : os.getenv('DB_HOST'),
    'db_port' : os.getenv('DB_PORT', 3306),
    'db_user' : os.getenv('DB_USER'),
    'db_password' : os.getenv('DB_PASSWORD'),
    'db_database' : os.getenv('DB_DATABASE'),
    # corporation_ids with all wallet entry ref_type's are tracked (dont need)
    # 'all_types_corporations' : os.getenv('ALL_TYPES_CORPORATIONS'),

    # WEB ONLY
    # 'eve_app_id': os.getenv('EVE_APP_ID'),
    # 'eve_app_secret': os.getenv('EVE_APP_SECRET'),
    # 'eve_app_callback': os.getenv('EVE_APP_CALLBACK'),
    # 'secret_key': os.getenv('SECRET_KEY'),
    # 'check_alliances': os.getenv('CHECK_ALLIANCES'),
    # 'check_corporations': os.getenv('CHECK_CORPORATIONS)'
}

for key in env_vars:
    if env_vars[key] is None:
        print(f'check_taxes: system environment variable {key} not configured')
        exit()

env_vars['api_base_url'] += '/api/app/v2/esi/latest'
api_auth_header = {'Authorization': 'Bearer ' + env_vars['api_key']}

try:
    brave_db = mysql.connector.connect(
        host=env_vars['db_host'],
        port=env_vars['db_port'],
        user=env_vars['db_user'],
        password=env_vars['db_password'],
        database=env_vars['db_database'],
    )
except mysql.connector.ProgrammingError as err:
    print(f'check_taxes: could not connect to database {err}')
    exit()
db_cursor = brave_db.cursor()

db_cursor.execute(
    '''
        SELECT id, character_id
        FROM corporations WHERE active = 1
    '''
)
corporation_data = db_cursor.fetchall()

print(corporation_data)
db_cursor.close()
brave_db.close()