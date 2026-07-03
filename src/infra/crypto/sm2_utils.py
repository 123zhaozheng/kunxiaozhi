"""SM2 encryption utilities compatible with Java Bouncy Castle (C1C2C3)."""

from gmssl import func, sm2, sm3


class SM2Utils:
    @staticmethod
    def encrypt(public_key_hex: str, data: str) -> str:
        """
        Encrypt plaintext for OA SSO using SM2 (C1C2C3, mode=0).

        Args:
            public_key_hex: 130-char hex public key starting with 04.
            data: UTF-8 string to encrypt.

        Returns:
            Uppercase hex ciphertext.
        """
        raw = data.encode("utf-8")
        if not public_key_hex.startswith("04") or len(public_key_hex) != 130:
            raise ValueError("公钥必须是 130 位十六进制且以 04 开头")

        sm2_crypt = sm2.CryptSM2(private_key=None, public_key=public_key_hex, mode=0)
        msg_hex = raw.hex()
        k = func.random_hex(sm2_crypt.para_len)
        c1_point_hex = sm2_crypt._kg(int(k, 16), sm2_crypt.ecc_table["g"])
        c1_hex = "04" + c1_point_hex

        xy_hex = sm2_crypt._kg(int(k, 16), sm2_crypt.public_key)
        t = sm3.sm3_kdf(xy_hex.encode("utf-8"), len(raw))
        if int(t, 16) == 0:
            raise RuntimeError("密钥派生函数(KDF)结果为 0，加密失败")

        form = f"%0{len(msg_hex)}x"
        c2_hex = form % (int(msg_hex, 16) ^ int(t, 16))

        para_len = sm2_crypt.para_len
        x2_hex = xy_hex[0:para_len]
        y2_hex = xy_hex[para_len : 2 * para_len]
        c3_input_bytes = bytes.fromhex(f"{x2_hex}{msg_hex}{y2_hex}")
        c3_hex = sm3.sm3_hash(list(c3_input_bytes))

        return f"{c1_hex}{c2_hex}{c3_hex}".upper()
