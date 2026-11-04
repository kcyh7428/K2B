import hashlib
import hmac
import os
from dataclasses import dataclass, field
from pathlib import Path


def hash_password(password):
    if len(password) < 14: raise ValueError('Use at least 14 characters')
    salt = os.urandom(16)
    return f'scrypt${salt.hex()}${hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1).hex()}'


def verify_password(password, encoded):
    try:
        algorithm, salt, value = encoded.split('$')
        if algorithm != 'scrypt': return False
        actual = hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1)
        return hmac.compare_digest(actual,bytes.fromhex(value))
    except (ValueError,TypeError):return False


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path.home()/'Library/Application Support/eSales CRM')
    demo: bool = False
    public_url: str = 'http://127.0.0.1:8765'
    password_hash: str = ''
    intake_secret: str = ''
    email_hook_secret: str = ''
    intake_environment: str = 'production'
    smtp_host: str = ''
    smtp_port: int = 587
    smtp_timeout_seconds: int = 60
    smtp_tls: bool = True
    smtp_user: str = ''
    smtp_password: str = ''
    smtp_from: str = ''
    delivery_enabled: bool = False

    @classmethod
    def from_env(cls):
        s=cls()
        for key in cls.__dataclass_fields__:
            raw=os.environ.get('CRM_'+key.upper())
            if raw is not None:
                value = raw in ('1','true','yes') if isinstance(getattr(s,key),bool) else int(raw) if isinstance(getattr(s,key),int) else Path(raw) if key=='data_dir' else raw
                setattr(s,key,value)
        return s
