from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


PAGE_SIZE = 4096
SALT_SIZE = 16
KEY_SIZE = 32
IV_SIZE = 16
HMAC_SIZE = 64
RESERVE = IV_SIZE + HMAC_SIZE
SQLITE_HEADER = b"SQLite format 3\x00"


def aes_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def decrypt_page(page: bytes, page_index: int, enc_key: bytes) -> bytes:
    if len(page) != PAGE_SIZE:
        raise ValueError(f"unexpected page size: {len(page)}")

    encrypted_start = SALT_SIZE if page_index == 0 else 0
    encrypted_end = PAGE_SIZE - RESERVE
    iv = page[encrypted_end : encrypted_end + IV_SIZE]
    ciphertext = page[encrypted_start:encrypted_end]

    plaintext = aes_cbc_decrypt(enc_key, iv, ciphertext)
    if page_index == 0:
        return SQLITE_HEADER + plaintext + (b"\x00" * RESERVE)
    return plaintext + (b"\x00" * RESERVE)


def decrypt_database(input_path: Path, output_path: Path, key_hex: str) -> None:
    raw = bytes.fromhex(key_hex)
    if len(raw) < KEY_SIZE:
        raise ValueError("key must contain at least 32 bytes of hex data")
    enc_key = raw[:KEY_SIZE]

    data = input_path.read_bytes()
    if len(data) % PAGE_SIZE != 0:
        raise ValueError(f"database size is not a multiple of {PAGE_SIZE}: {len(data)}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as out:
        for page_index in range(len(data) // PAGE_SIZE):
            page = data[page_index * PAGE_SIZE : (page_index + 1) * PAGE_SIZE]
            out.write(page if all(byte == 0 for byte in page) else decrypt_page(page, page_index, enc_key))
