"""
    This is basically the reference OAuth implementation from CCP
        https://developers.eveonline.com/docs/services/sso/
        we're using the PKCE variant with a code challenge

    modified / added parts
     - use our programs scope and id for the api key request url
     - our localhost listener for the key response by ccp

    The entire purpose of this module is to let us ask the user to tell ccp to give the program an API key, securely, without us needing your login/password. That requries a bit of back and forth...
"""

import requests
import common
import urllib
import base64
import socket
import hashlib
import secrets
from jose import jwt
from datetime import datetime
from typing import Union

SSO_META_DATA_URL = "https://login.eveonline.com/.well-known/oauth-authorization-server"
JWK_ALGORITHM = "RS256"
JWK_ISSUERS = ("login.eveonline.com", "https://login.eveonline.com")
JWK_AUDIENCE = "EVE Online"

SEND_MAIL_SCOPE = (
    "esi-mail.send_mail.v1 "
)
SEND_MAIL_SCOPE = (
    "esi-wallet.read_corporation_wallet.v1 "
    "esi-wallet.read_corporation_wallets.v1 "
)

# this one is never sent, used to verify the communication between us
CODE_VERIFIER = base64.urlsafe_b64encode(secrets.token_bytes(32))
LAST_STATE_UPDATE = datetime.now()
# this one is used to verify responses match requests
UNIQUE_STATE = base64.urlsafe_b64encode(secrets.token_bytes(32))
awaiting_response = False

def create_access_token(scope=SEND_MAIL_SCOPE) -> Union[dict, None]:
    """Prints the link to authorize ESI token for this app and starts a localhost server that listens for provided authorization and uses that authorization to request an access token.
    """
    global awaiting_response
    awaiting_response = True
    print(create_esi_auth_url(scope=scope))
    token = run_server()
    return token

def run_server() -> Union[dict, None]:
    host = 'localhost'
    port = common.config["localhost_port"]

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind((host, port))
    server_socket.listen(1)

    server_socket.settimeout(3.0)
    token_created = None
    print(f'Server listening on {host}:{port}')
    try:
        while awaiting_response:
            client_socket = None
            try:
                client_socket, client_address = server_socket.accept()
                token_created = handle_request(client_socket, client_address)
                print(f'Client connected: {client_address[0]}:{client_address[1]}')
            except socket.timeout:
                # print("Timeout")
                pass
            except KeyboardInterrupt:
                if client_socket:
                    client_socket.close()
                print("Server closed with KeyboardInterrupt!")
                break
    except KeyboardInterrupt:
        print("Server closed with KeyboardInterrupt!")
        server_socket.close()
    print("API key received from CCP! Server closed.")
    return token_created

def handle_request(client_socket, client_address):
    data = client_socket.recv(1024)
    request = data.decode()
    try:
        http_method, route = request.split(' ')[0:2]
    except:
        response = 'HTTP/1.1 404 Not Found\r\nContent-Type: text/html\r\n\r\nPage not found.'
        client_socket.sendall(response.encode('utf-8'))
        client_socket.close()

    token_created = None
    callback_route = '/callback/'
    if route[0:len(callback_route)] == callback_route:
        param_strs = route[len(callback_route)+1:].split("&")
        params = {}
        for p_str in param_strs:
            param = p_str.split('=')
            params[param[0]] = param[1]
        authentication_code = params["code"]
        state_received = urllib.parse.unquote_to_bytes(params["state"])
        if state_received != UNIQUE_STATE:
            print("Unique state mismatch!")
            print(f"received :{state_received}")
            print(f"local :{UNIQUE_STATE}")
        token_created = request_token(authentication_code)
        response = 'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\nAuthentication successful.'
    else:
        response = 'HTTP/1.1 404 Not Found\r\nContent-Type: text/html\r\n\r\nPage not found.'
    
    client_socket.sendall(response.encode('utf-8'))
    client_socket.close()
    global awaiting_response
    awaiting_response = False
    return token_created

def update_unique_state():
    # you can use more complex methods, this simply changes the state hourly
    # all auth urls generated before that time become invalid
    global UNIQUE_STATE
    global LAST_STATE_UPDATE
    if (datetime.now() - LAST_STATE_UPDATE).seconds >= 3600:
        LAST_STATE_UPDATE = datetime.now()
        UNIQUE_STATE = base64.urlsafe_b64encode(secrets.token_bytes(32))
    return UNIQUE_STATE
def create_code_verifier():
    global CODE_VERIFIER
    CODE_VERIFIER = base64.urlsafe_b64encode(secrets.token_bytes(32))
def create_code_challenge():
    global CODE_VERIFIER
    if CODE_VERIFIER is None:
        create_code_verifier()
    
    m = hashlib.sha256()
    m.update(CODE_VERIFIER)
    d = m.digest()
    code_challenge = base64.urlsafe_b64encode(d).decode().replace("=", "")
    return code_challenge

def create_esi_auth_url(
    scope=SEND_MAIL_SCOPE
):
    client_id = common.config["client_id"]
    base_auth_url = "https://login.eveonline.com/v2/oauth/authorize/"
    params = {
        "response_type": "code",
        "redirect_uri": f"http://localhost:{common.config["localhost_port"]}/callback/",
        "client_id": client_id,
        "scope": scope,
        "state": update_unique_state(),
        "code_challenge": create_code_challenge(),
        "code_challenge_method": "S256"
    }
    string_params = urllib.parse.urlencode(params)
    string_params = string_params.replace("+", "%20")
    full_auth_url = "{}?{}".format(base_auth_url, string_params)
    return full_auth_url

def authenticate(scope=SEND_MAIL_SCOPE):
    esi_auth_url = create_esi_auth_url(scope=scope)
    print("\nOpen the following link in your browser:\n\n {} \n\n Once you "
          "have logged in as a character you will get redirected to "
          "http://localhost/callback/.".format(esi_auth_url))

def request_token(authentication_code) -> dict:
    client_id = common.config["client_id"]
    global CODE_VERIFIER
    form_values = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": authentication_code,
        "code_verifier": CODE_VERIFIER
    }
    res = send_token_request(form_values)
    jres = res.json()
    return jres

def validate_eve_jwt(token: str) -> dict:
    """Validate a JWT access token retrieved from the EVE SSO.

    Args:
        token: A JWT access token originating from the EVE SSO
    Returns:
        The contents of the validated JWT access token if there are no errors
    Raises:
        ExpiredSignatureError: The JWT token has expired
        JWTError: The JWT token was invalid
        RuntimeError: other
    
    """
    # fetch JWKs URL from meta data endpoint
    res = requests.get(SSO_META_DATA_URL)
    res.raise_for_status()
    data = res.json()
    try:
        jwks_uri = data["jwks_uri"]
    except KeyError:
        raise RuntimeError(
            f"Invalid data received from the SSO meta data endpoint: {data}"
        ) from None

    # fetch JWKs from endpoint
    res = requests.get(jwks_uri)
    res.raise_for_status()
    data = res.json()
    try:
        jwk_sets = data["keys"]
    except KeyError:
        raise RuntimeError(
            f"Invalid data received from the the jwks endpoint: {data}"
        ) from None

    # pick the JWK with the requested alogorithm
    jwk_set = [item for item in jwk_sets if item["alg"] == JWK_ALGORITHM].pop()

    # try to decode the token and validate it against expected values
    # will raise JWT exceptions if decoding fails or expected values do not match
    contents = jwt.decode(
        token=token,
        key=jwk_set,
        algorithms=jwk_set["alg"],
        issuer=JWK_ISSUERS,
        audience=JWK_AUDIENCE,
    )
    contents["sub"] = int(contents["sub"].replace('CHARACTER:EVE:', ''))
    return contents

def send_token_request(form_values, add_headers={}):
    """Sends a request for an authorization token to the EVE SSO.

    Args:
        form_values: A dict containing the form encoded values that should be
                     sent with the request
        add_headers: A dict containing additional headers to send
    Returns:
        requests.Response: A requests Response object
    """

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Host": "login.eveonline.com",
    }

    if add_headers:
        headers.update(add_headers)

    res = requests.post(
        "https://login.eveonline.com/v2/oauth/token",
        data=form_values,
        headers=headers,
    )
    res.raise_for_status()

    return res

def refresh_token(refresh_token, client_id):
    form_values = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    sso_response = send_token_request(form_values)

    if sso_response.status_code == 200:
        return sso_response.json()
        
    return None


"""
    ccp token response fields:
        access_token
            scp
            jti
            kid
            sub
            azp
            tenant
            tier
            region
            aud
            name
            owner
            exp
            iat
            iss
        expires_in
        token_type
        refresh_token
"""