"""
Auth helpers — password hashing, JWT, get_current_user dependency.
MongoDB version: looks up user by _id (ObjectId) stored in token.
"""

import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from bson import ObjectId

from app.db.database import users_col

SECRET_KEY              = os.getenv("SECRET_KEY", "nexora-dev-secret-change-in-production")
ALGORITHM               = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24 * 7   # 7 days

pwd_context   = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire    = datetime.utcnow() + (expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
    to_encode["exp"] = expire
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """
    FastAPI dependency.
    Decodes JWT, fetches user doc from MongoDB, returns it.
    User doc has string _id field added as 'id' for convenience.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload    = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str: str = payload.get("sub")
        if not user_id_str:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = users_col().find_one({"_id": ObjectId(user_id_str)})
    if not user:
        raise credentials_exception

    # Add string id for easy use downstream
    user["id"] = str(user["_id"])
    return user