from __future__ import annotations

import base64
import hashlib
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class WeComAppCryptoError(ValueError):
    pass


@dataclass(frozen=True)
class WeComAppMessage:
    from_user: str
    to_user: str
    msg_type: str
    content: str
    msg_id: str = ""
    agent_id: str = ""


class WeComAppCrypto:
    def __init__(self, *, token: str, encoding_aes_key: str, corp_id: str) -> None:
        self.token = str(token or "").strip()
        self.corp_id = str(corp_id or "").strip()
        key_text = str(encoding_aes_key or "").strip()
        if len(key_text) != 43:
            raise WeComAppCryptoError("EncodingAESKey 必须是 43 位")
        self.key = base64.b64decode(key_text + "=")
        if len(self.key) != 32:
            raise WeComAppCryptoError("EncodingAESKey 解码后长度不正确")

    def verify_signature(self, signature: str, timestamp: str, nonce: str, encrypted: str) -> bool:
        pieces = [self.token, str(timestamp or ""), str(nonce or ""), str(encrypted or "")]
        digest = hashlib.sha1("".join(sorted(pieces)).encode("utf-8")).hexdigest()
        return digest == str(signature or "")

    def decrypt(self, encrypted: str) -> str:
        try:
            raw = base64.b64decode(str(encrypted or ""))
        except Exception as exc:
            raise WeComAppCryptoError("Encrypt 字段不是合法 base64") from exc
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(self.key[:16]))
        decryptor = cipher.decryptor()
        plain = decryptor.update(raw) + decryptor.finalize()
        plain = self._unpad(plain)
        if len(plain) < 20:
            raise WeComAppCryptoError("解密内容长度异常")
        msg_len = struct.unpack("!I", plain[16:20])[0]
        xml_bytes = plain[20 : 20 + msg_len]
        corp_id = plain[20 + msg_len :].decode("utf-8", errors="ignore")
        if self.corp_id and corp_id != self.corp_id:
            raise WeComAppCryptoError("CorpID 不匹配")
        return xml_bytes.decode("utf-8")

    @staticmethod
    def _unpad(value: bytes) -> bytes:
        if not value:
            raise WeComAppCryptoError("解密内容为空")
        pad = value[-1]
        if pad < 1 or pad > 32:
            pad = 0
        return value[: len(value) - pad]


def encrypted_text_from_xml(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise WeComAppCryptoError("XML 格式不正确") from exc
    return str(root.findtext("Encrypt") or "").strip()


def parse_wecom_app_message(xml_text: str) -> WeComAppMessage:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise WeComAppCryptoError("解密后的 XML 格式不正确") from exc
    msg_type = str(root.findtext("MsgType") or "").strip()
    content = ""
    if msg_type == "text":
        content = str(root.findtext("Content") or "").strip()
    elif msg_type == "voice":
        content = str(root.findtext("Recognition") or "").strip()
    return WeComAppMessage(
        from_user=str(root.findtext("FromUserName") or "").strip(),
        to_user=str(root.findtext("ToUserName") or "").strip(),
        msg_type=msg_type,
        content=content,
        msg_id=str(root.findtext("MsgId") or "").strip(),
        agent_id=str(root.findtext("AgentID") or "").strip(),
    )
