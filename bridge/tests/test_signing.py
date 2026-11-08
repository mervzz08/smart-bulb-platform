import pytest

from bulb_bridge.signing import SignatureError, compute, sign_header, verify

SECRET = "s" * 20
BODY = b'{"id":"evt_1","type":"order.paid"}'
NOW = 1_700_000_000


def test_valid_signature_round_trips():
    assert verify([SECRET], BODY, sign_header(SECRET, BODY, NOW), now=NOW) == NOW


@pytest.mark.parametrize("header, reason", [
    (None, "missing"),
    ("", "missing"),
    ("garbage", "malformed"),
    (f"t={NOW}", "malformed"),
    (f"v1={'0' * 64}", "malformed"),
    (f"t=abc,v1={'0' * 64}", "malformed"),
])
def test_bad_headers_are_rejected(header, reason):
    with pytest.raises(SignatureError, match=reason):
        verify([SECRET], BODY, header, now=NOW)


def test_tampered_body_is_rejected():
    header = sign_header(SECRET, BODY, NOW)
    with pytest.raises(SignatureError, match="mismatch"):
        verify([SECRET], BODY.replace(b"paid", b"refunded"), header, now=NOW)


def test_wrong_secret_is_rejected():
    with pytest.raises(SignatureError, match="mismatch"):
        verify(["other-secret-000000"], BODY, sign_header(SECRET, BODY, NOW), now=NOW)


@pytest.mark.parametrize("skew", [301, -301])
def test_replayed_or_future_timestamps_are_rejected(skew):
    with pytest.raises(SignatureError, match="tolerance"):
        verify([SECRET], BODY, sign_header(SECRET, BODY, NOW), now=NOW + skew)


def test_changing_the_timestamp_breaks_the_signature():
    # an attacker cannot refresh an old request by editing t=
    old_sig = compute(SECRET, NOW - 3600, BODY)
    with pytest.raises(SignatureError, match="mismatch"):
        verify([SECRET], BODY, f"t={NOW},v1={old_sig}", now=NOW)


def test_secret_rotation_accepts_old_and_new():
    old, new = "old-secret-00000000", "new-secret-00000000"
    assert verify([new, old], BODY, sign_header(old, BODY, NOW), now=NOW) == NOW
    two_sigs = f"t={NOW},v1={'0' * 64},v1={compute(new, NOW, BODY)}"
    assert verify([new], BODY, two_sigs, now=NOW) == NOW
