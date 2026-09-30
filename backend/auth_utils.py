import hmac
import hashlib
import urllib.parse
from datetime import datetime
import os
from passlib.context import CryptContext
from jose import jwt

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def validate_telegram_init_data(init_data: str, bot_token: str, expiration_seconds: int = 300) -> dict | None:
    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data))
        if "hash" not in parsed_data:
            return None

        received_hash = parsed_data.pop("hash")
        
        # Check expiration
        if "auth_date" not in parsed_data:
            return None
            
        auth_date = int(parsed_data["auth_date"])
        time_diff = datetime.now().timestamp() - auth_date
        
        # Must not be older than expiration_seconds, and must not be too far in the future
        if time_diff > expiration_seconds or time_diff < -10:
            return None

        # Reconstruct the data-check-string
        data_check_arr = []
        for key, value in sorted(parsed_data.items()):
            data_check_arr.append(f"{key}={value}")
        data_check_string = "\n".join(data_check_arr)

        # Generate HMAC hash
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        if hmac.compare_digest(calculated_hash, received_hash):
            return parsed_data
        return None
    except Exception:
        return None

from datetime import datetime, timedelta

def create_access_token(data: dict, expires_delta: timedelta = timedelta(days=7)):
    to_encode = data.copy()
    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise ValueError("JWT_SECRET environment variable is not set")
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, secret, algorithm="HS256")
    return encoded_jwt
