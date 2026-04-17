import base64
import binascii
import json
import logging
from urllib.parse import unquote

from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5, AES
from Crypto.Util.Padding import unpad

logger = logging.getLogger(__name__)

AES_IV = b"2584532147856215"

RSA_PRIVATE_KEY_PEM = """-----BEGIN PRIVATE KEY-----
MIIEpAIBAAKCAQEAobg5iEask2geDtxuvqFnan3O4PIG1mFRP5U/rNFa1B9DQ8aH
XLgG4LDllTRxRYNIUDXfKCUxaHcfZDSBIfIHzEvp7oE+5O+ynbczH2SjXJfN8VGA
5NZPGbvKQjwdkRRgoQdcEVZUYe7FWxeFSIRNU2N20dfVjpv7rtlMTKTBBaaFpnOb
6oZEiTwK2mAuHTD1GfQ2RAbGZs59gQBG2MDycSf6i27QqbW0I4AOwm7mIhWe42Y+
//aQRzF3qc6AMNATVyByWEIK8jULyRuR0M5B+f2lU1vWBf21KnN4mlMdDSHRliPf
WRrXrvKrAc2FymE4/DiZ01VB3uy3M4TV/rgdywIDAQABAoIBABi6Bs3v5G4rcsEZ
8jLikeHl945MY0A/JAGhS9mcLxOU7h98SPEj0CVl1syf9pvGzXU6L3M/cJUE9b9I
CeCLVabmio+loly1y60yuDXaGOJM8beumxMiM3j/ThcfgvPOVlH4wpqCBSfuLq3V
ZFMoq3wPDrlaE3SZI/vhjLmBTWQUCeuUgPDHsyztsVp9hiJfdlXlLAt7a5Aw63A5
mYvyqIi0DcdVhVti6kZAdhkvbA2RSQyTajkgxoXcpe8PXDMbgT9WV2gyTJz832/n
Dw3HuYkwMS89CUoUa2P5HuBPErxca/0Ydv7V7xRfyNF+naUEIpTYVsOcbhyQFJS
sf1ylwpkCgYEA41Ziuelp9rFnHz//ZSHeEm8SL8KTmRbnFTNVWr45ZsSfHzIVOUdr
nKnlz+yu5vcMwytSBFwuBlgSBHWAFH+42Su0J4eI06/77pwriGgESjZzkvtsT3LI
7YWvctNMo4XqERa3ouTFIjkVFfsXHEnW7Zx9+amQvemt7DuZnpRfLtkCgYEAthvw
HhKEhtluizUIpICacGJDenZgO3ZxZk6WHpwtrqo7qkZfif/VrgkixpqlIJjx0vj4
jafbtVyNPwSZ9VfcRFq44LcOPkOg6ngf2ao42WcW5LebGlOmr0lk82+gzoojkrqN
0PE9LCjbD47r4TcFD6GeTaVFzmquzYEBYMyC00MCgYAbyfR5e0G7qQXM+Rqz9wbZ
RAB6HBPEs9r9aW/2jqgfmstEme+kN8m8tbvkxa6/htVligcVh1sM5XkWWHKWjuI+
kawM5PFhxvJJwYdEvko/9BX+koMz1vkep6fBpniIyJbLDfbWj5ZVT5r3O+EgURpX
ozh26zZJMKZU6RgnHUXhSQKBgQCaHFh+yoL2r2i6S74toFuSAcZDC4xypdBfmN+3
tcl/B7cIaReO7D9DUZ3pXpOhW21Cccm97zCicVli3B0CIEFaY0ATgzZ9gLPb2J5z
kHcdm/0mvy52ABaOPlk9HdmDECn8kP1UteJjzYtcxkFdzTbuPIKACP5jKasWZDbr
WQbZiwKBgQCIgGepO63kS+wSV/iswXTnWx8ZIYNQ+JrsDyQyJTLhfTGYhvwe0+DC
bnt96KL0TZ6HVgT5RYhlzcD4hZJQNqfdjXHP9Bredt7AehiiMkU3B+O81JBM0mro
R0vhtyqId37PhFHiRtErxgbfOEKKPAVbWXaAPrmc4DkDqNFdedZIzw==
-----END PRIVATE KEY-----"""

_rsa_key = RSA.import_key(RSA_PRIVATE_KEY_PEM)
_rsa_cipher = PKCS1_v1_5.new(_rsa_key)


def decrypt_rsa(encrypted_b64: str) -> str:
    decoded = unquote(encrypted_b64)
    ciphertext = base64.b64decode(decoded)
    plaintext = _rsa_cipher.decrypt(ciphertext, sentinel=b"DECRYPTION_FAILED")
    if plaintext == b"DECRYPTION_FAILED":
        raise ValueError("RSA decryption failed")
    return plaintext.decode("utf-8")


def decrypt_aes(hex_data: str, aes_key: str) -> str:
    raw_bytes = binascii.unhexlify(hex_data)
    b64_data = base64.b64encode(raw_bytes).decode("utf-8")
    cipher = AES.new(
        aes_key.encode("utf-8"),
        AES.MODE_CBC,
        iv=AES_IV,
    )
    padded = base64.b64decode(b64_data)
    decrypted = unpad(cipher.decrypt(padded), AES.block_size)
    return decrypted.decode("utf-8")


def decrypt_response(api_response: dict) -> dict:
    private_key_enc = api_response.get("privateKey")
    data_hex = api_response.get("data")

    if not private_key_enc or not isinstance(data_hex, str):
        return api_response

    try:
        aes_key = decrypt_rsa(private_key_enc)
        plaintext = decrypt_aes(data_hex, aes_key)
        return json.loads(plaintext)
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        return api_response
