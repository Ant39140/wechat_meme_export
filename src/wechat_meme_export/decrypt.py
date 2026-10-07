from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


PAGE_SIZE = 4096
SALT_SIZE = 16
KEY_SIZE = 32
IV_SIZE = 16
HMAC_SIZE = 64
RESERVE = IV_SIZE + HMAC_SIZE
RESERVE_SIZES = (RESERVE, 48, 64, 36, 16)
SQLITE_HEADER = b"SQLite format 3\x00"
PAGE_MAC_ITERATIONS = 2
MAC_SALT_XOR = 0x3A


def aes_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def verify_first_page(page: bytes, enc_key: bytes) -> bool:
    if len(page) < PAGE_SIZE or len(enc_key) != KEY_SIZE:
        return False

    salt = page[:SALT_SIZE]
    if salt == b"\x00" * SALT_SIZE or page.startswith(SQLITE_HEADER):
        return False

    # Weixin 4.x stores the raw database key; only the page-MAC key is derived.
    authenticated_end = PAGE_SIZE - RESERVE + IV_SIZE
    hmac_key = hashlib.pbkdf2_hmac(
        "sha512",
        enc_key,
        bytes(value ^ MAC_SALT_XOR for value in salt),
        PAGE_MAC_ITERATIONS,
        dklen=32,
    )
    expected = hmac.new(
        hmac_key,
        page[SALT_SIZE:authenticated_end] + (1).to_bytes(4, "little"),
        hashlib.sha512,
    ).digest()
    actual = page[authenticated_end : authenticated_end + HMAC_SIZE]
    return hmac.compare_digest(expected, actual)


def detect_page_reserve(page: bytes, enc_key: bytes) -> int | None:
    if len(page) < PAGE_SIZE or len(enc_key) != KEY_SIZE:
        return None
    if verify_first_page(page, enc_key):
        return RESERVE

    for reserve in RESERVE_SIZES:
        encrypted_end = PAGE_SIZE - reserve
        iv = page[encrypted_end : encrypted_end + IV_SIZE]
        ciphertext = page[SALT_SIZE:encrypted_end]
        try:
            plaintext = aes_cbc_decrypt(enc_key, iv, ciphertext)
        except ValueError:
            continue
        if (
            plaintext[:2] == b"\x10\x00"
            and plaintext[2] in (1, 2)
            and plaintext[3] in (1, 2)
            and plaintext[4:8] == bytes((reserve, 64, 32, 32))
        ):
            return reserve
    return None


def decrypt_page(page: bytes, page_index: int, enc_key: bytes, reserve: int = RESERVE) -> bytes:
    if len(page) != PAGE_SIZE:
        raise ValueError(f"unexpected page size: {len(page)}")
    if reserve not in RESERVE_SIZES:
        raise ValueError(f"unsupported SQLCipher reserve size: {reserve}")

    encrypted_start = SALT_SIZE if page_index == 0 else 0
    encrypted_end = PAGE_SIZE - reserve
    iv = page[encrypted_end : encrypted_end + IV_SIZE]
    ciphertext = page[encrypted_start:encrypted_end]

    plaintext = aes_cbc_decrypt(enc_key, iv, ciphertext)
    if page_index == 0:
        return SQLITE_HEADER + plaintext + (b"\x00" * reserve)
    return plaintext + (b"\x00" * reserve)


def decrypt_database(input_path: Path, output_path: Path, key_hex: str) -> None:
    raw = bytes.fromhex(key_hex)
    if len(raw) < KEY_SIZE:
        raise ValueError("key must contain at least 32 bytes of hex data")
    enc_key = raw[:KEY_SIZE]

    data = input_path.read_bytes()
    if len(data) % PAGE_SIZE != 0:
        raise ValueError(f"database size is not a multiple of {PAGE_SIZE}: {len(data)}")
    reserve = detect_page_reserve(data[:PAGE_SIZE], enc_key)
    if reserve is None:
        raise ValueError("key did not match a supported SQLCipher page format")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as out:
        for page_index in range(len(data) // PAGE_SIZE):
            page = data[page_index * PAGE_SIZE : (page_index + 1) * PAGE_SIZE]
            out.write(
                page
                if all(byte == 0 for byte in page)
                else decrypt_page(page, page_index, enc_key, reserve)
            )
