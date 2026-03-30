"""Auth Router — MongoDB version"""

from datetime import datetime
from fastapi import APIRouter, HTTPException, status, Depends
from bson import ObjectId

from app.db.database import users_col
from app.db.auth import hash_password, verify_password, create_access_token, get_current_user
from app.schemas.schemas import RegisterRequest, LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(body: RegisterRequest):
    if users_col().find_one({"email": body.email}):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered.")

    doc = {
        "name":             body.name,
        "email":            body.email,
        "hashed_password":  hash_password(body.password),
        "camera_consent":   body.camera_consent,
        "created_at":       datetime.utcnow(),
    }
    result  = users_col().insert_one(doc)
    user_id = str(result.inserted_id)
    token   = create_access_token({"sub": user_id})
    return TokenResponse(access_token=token, user_id=user_id, name=body.name)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest):
    user = users_col().find_one({"email": body.email})
    if not user or not verify_password(body.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = str(user["_id"])
    token   = create_access_token({"sub": user_id})
    return TokenResponse(access_token=token, user_id=user_id, name=user["name"])


@router.get("/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "id":             current_user["id"],
        "name":           current_user["name"],
        "email":          current_user["email"],
        "camera_consent": current_user.get("camera_consent", False),
        "created_at":     current_user.get("created_at"),
    }