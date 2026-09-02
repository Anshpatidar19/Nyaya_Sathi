import json

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from . import kanoon, models, reasoning, schemas
from .auth import create_access_token, get_current_user, hash_password, verify_password
from .database import Base, SessionLocal, engine, get_db

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Nyaya Sathi API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------- Auth ----------------

@app.post("/auth/register", response_model=schemas.TokenOut, status_code=status.HTTP_201_CREATED)
def register(payload: schemas.UserCreate, db: Session = Depends(get_db)):
    existing = db.query(models.User).filter(models.User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="An account with this email already exists.")

    user = models.User(
        name=payload.name,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        state=payload.state,
        preferred_language=payload.preferred_language,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(subject=user.email)
    return schemas.TokenOut(access_token=token, user=schemas.UserOut.model_validate(user))


@app.post("/auth/login", response_model=schemas.TokenOut)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # OAuth2PasswordRequestForm uses "username" as the field name; we treat it as email.
    user = db.query(models.User).filter(models.User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )
    token = create_access_token(subject=user.email)
    return schemas.TokenOut(access_token=token, user=schemas.UserOut.model_validate(user))


@app.get("/auth/me", response_model=schemas.UserOut)
def me(current_user: models.User = Depends(get_current_user)):
    return current_user


# ---------------- Ask (core Q&A) ----------------

@app.post("/ask", response_model=schemas.AskResponse)
async def ask(
    payload: schemas.AskRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    answer = await reasoning.answer_question(payload.question, payload.state or current_user.state)

    log = models.QueryLog(
        user_id=current_user.id,
        question=payload.question,
        answer_title=answer.title,
        answer_body=answer.body,
        citations_json=json.dumps([c.model_dump() for c in answer.citations]),
    )
    db.add(log)
    db.commit()

    return answer


@app.get("/ask/history", response_model=list[schemas.QueryHistoryItem])
def ask_history(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return (
        db.query(models.QueryLog)
        .filter(models.QueryLog.user_id == current_user.id)
        .order_by(models.QueryLog.created_at.desc())
        .limit(50)
        .all()
    )


# ---------------- Indian Kanoon passthrough (for the research/citation view) ----------------

@app.get("/kanoon/search", response_model=list[schemas.KanoonSearchResult])
async def kanoon_search(q: str, current_user: models.User = Depends(get_current_user)):
    try:
        results = await kanoon.search(q)
    except kanoon.KanoonError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return results


@app.get("/kanoon/doc/{docid}")
async def kanoon_doc(docid: str, current_user: models.User = Depends(get_current_user)):
    try:
        return await kanoon.get_document(docid)
    except kanoon.KanoonError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
