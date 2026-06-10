import os
from typing import Union

from flask import Flask, Response

from pages.Auth import Auth
from pages.Index import Index
from pages.Tokens import Tokens

from werkzeug.wrappers import Response as wzResponse

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')
app.logger.setLevel(os.getenv('UWSGI_LOG_LEVEL', 'ERROR').upper())

@app.route('/')
def index() -> Union[str, Response]:
    return Index().show()


@app.route('/tokens')
def tokens() -> Union[str, wzResponse]:
    return Tokens(app).show()

@app.route('/tokens/set_corp_attr', methods=['POST'])
def set_corp_attr() -> Union[str,wzResponse]:
    return Tokens(app).set_corp_attr()
@app.route('/tokens/set_config', methods=['POST'])
def set_config() -> Union[str,wzResponse]:
    return Tokens(app).set_config()

@app.route('/tokens/update_ceos', methods=['POST'])
def update_ceos() -> wzResponse:
    return Tokens(app).update_ceos()

@app.route('/tokens/update_wallets', methods=['POST'])
def update_wallets() -> wzResponse:
    return Tokens(app).update_wallets()

@app.route('/tokens/show_starting_balance_editor', methods=['POST'])
def show_starting_balance_editor() -> Union[str,wzResponse]:
    return Tokens(app).show_starting_balance_editor()
@app.route('/tokens/set_starting_balance', methods=['POST'])
def set_starting_balance() -> Union[str,wzResponse]:
    return Tokens(app).set_starting_balance()
@app.route('/tokens/show_owner_editor', methods=['POST'])
def show_owner_editor() -> Union[str,wzResponse]:
    return Tokens(app).show_owner_editor()

@app.route('/tokens/update_tax_records', methods=['POST'])
def update_tax_records() -> wzResponse:
    return Tokens(app).update_tax_records()

@app.route('/tokens/test_mail', methods=['POST'])
def test_mail() -> Union[str,wzResponse]:
    return Tokens(app).test_mail()
@app.route('/tokens/tax_evemail', methods=['POST'])
def tax_evemail() -> wzResponse:
    return Tokens(app).tax_evemail()
@app.route('/tokens/tax_evemails', methods=['POST'])
def tax_evemails() -> wzResponse:
    return Tokens(app).tax_evemails()
@app.route('/tokens/cancel_evemails', methods=['POST'])
def cancel_evemails() -> wzResponse:
    return Tokens(app).cancel_evemails()

@app.route('/auth/login')
def auth_login() -> str:
    return Auth.login()


@app.route('/auth/redirect')
def auth_redirect() -> Response:
    return Auth().redirect()


@app.route('/auth/callback')
def auth_callback() -> Response:
    return Auth().callback()


@app.route('/auth/logout')
def auth_logout() -> Response:
    return Auth.logout()
