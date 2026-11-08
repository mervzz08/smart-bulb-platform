import time

import jwt

from bulb_bridge.auth import AUDIENCE, ISSUER

from .conftest import CLIENT_ID, CLIENT_SECRET, JWT_SECRET, READ_KEY, WRITE_KEY, bearer

FORM = {"grant_type": "client_credentials"}


def test_token_with_basic_auth(client):
    r = client.post("/oauth/token", data=FORM, auth=(CLIENT_ID, CLIENT_SECRET))
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 900
    assert r.headers["cache-control"] == "no-store"
    assert set(body["scope"].split()) == {"devices:read", "devices:write", "events:read"}


def test_token_with_form_credentials(client):
    r = client.post("/oauth/token", data={**FORM, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
    assert r.status_code == 200


def test_wrong_secret_and_unknown_client_look_the_same(client):
    for creds in [(CLIENT_ID, "wrong"), ("nobody", CLIENT_SECRET)]:
        r = client.post("/oauth/token", data=FORM, auth=creds)
        assert r.status_code == 401
        assert r.json()["error"] == "invalid_client"
        assert "WWW-Authenticate" in r.headers


def test_token_request_errors(client):
    assert client.post("/oauth/token", data={"grant_type": "password"},
                       auth=(CLIENT_ID, CLIENT_SECRET)).json()["error"] == "unsupported_grant_type"
    assert client.post("/oauth/token", data={**FORM, "scope": "devices:admin"},
                       auth=(CLIENT_ID, CLIENT_SECRET)).json()["error"] == "invalid_scope"
    r = client.post("/oauth/token", json=FORM, auth=(CLIENT_ID, CLIENT_SECRET))
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"
    assert client.post("/oauth/token", data=FORM).status_code == 401


def test_bearer_token_grants_its_scopes(client):
    assert client.get("/devices", headers=bearer(client)).status_code == 200


def test_narrowed_token_cannot_write(client):
    headers = bearer(client, scope="devices:read")
    assert client.get("/devices", headers=headers).status_code == 200
    r = client.post("/devices/cct-1/commands", json={"state": "OFF"}, headers=headers)
    assert r.status_code == 403
    assert "insufficient_scope" in r.headers["www-authenticate"]


def _token(**overrides):
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": CLIENT_ID, "scope": "devices:read",
              "iat": now, "exp": now + 60, **overrides}
    return jwt.encode(claims, JWT_SECRET, algorithm="HS256")


def test_rejected_tokens(client):
    bad = [
        _token(exp=int(time.time()) - 1),                        # expired
        _token(aud="someone-else"),                             # wrong audience
        _token(iss="evil"),                                     # wrong issuer
        jwt.encode({"sub": "x", "scope": "devices:read"}, "wrong-key-" * 4, algorithm="HS256"),
        jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "scope": "devices:read",
                    "iat": 0, "exp": 2**31}, None, algorithm="none"),  # alg=none attack
        _token()[:-4] + "AAAA",                                 # tampered signature
    ]
    for token in bad:
        r = client.get("/devices", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, token


def test_api_keys(client):
    assert client.get("/devices", headers={"X-API-Key": READ_KEY}).status_code == 200
    assert client.post("/devices/cct-1/commands", json={"state": "OFF"},
                       headers={"X-API-Key": READ_KEY}).status_code == 403
    assert client.post("/devices/cct-1/commands", json={"state": "OFF"},
                       headers={"X-API-Key": WRITE_KEY}).status_code == 200
    assert client.get("/devices", headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get("/devices").status_code == 401
