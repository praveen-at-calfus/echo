"""``/auth`` — register, login, and "who am I".

Plain English: the login desk. New people can sign themselves up as feedback-
givers (GEN-POP); everyone logs in here to get a token; a logged-in caller can
ask who they are. COMPANY (staff) accounts are never created here — they come
from ``python -m echo.auth`` — so public sign-up can't grant itself analytics
access.

``/auth/login`` uses the OAuth2 password form (``username`` = email) so FastAPI's
Swagger UI shows an Authorize button that just works.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from echo import config
from echo.api import deps
from echo.api.schemas import RegisterIn, TokenOut, UserOut
from echo.auth import security, service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: RegisterIn) -> UserOut:
    """Self-register a GEN-POP account. Returns the created account, not a token -
    registration no longer logs the user in; they log in separately afterward."""
    try:
        user = service.create_user(
            deps.get_engine(),
            email=body.email,
            password=body.password,
            role=config.ROLE_GEN_POP,  # public sign-up is always gen_pop, never company
            full_name=body.full_name,
        )
    except service.EmailExistsError:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered") from None
    return UserOut(id=user["id"], email=user["email"], role=user["role"],
                   full_name=user.get("full_name"))


@router.post("/login", response_model=TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends()) -> TokenOut:
    """Exchange email (``username``) + password for a bearer token."""
    user = service.authenticate(deps.get_engine(), form.username, form.password)
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = security.create_access_token(user["id"], user["role"])
    return TokenOut(access_token=token, role=user["role"])


@router.get("/me", response_model=UserOut)
def me(user: dict = Depends(deps.get_current_user)) -> UserOut:
    """Return the authenticated caller's own account (no password hash)."""
    return UserOut(id=user["id"], email=user["email"], role=user["role"],
                   full_name=user.get("full_name"))
