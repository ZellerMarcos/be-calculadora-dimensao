import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import router

load_dotenv()

app = FastAPI(title="Plúvio - API NBR 10844", version="0.2.0")
cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, error: RequestValidationError):
    first_error = error.errors()[0] if error.errors() else {}
    field = ".".join(str(part) for part in first_error.get("loc", ()) if part != "body")
    raw_message = first_error.get("msg", "")
    if raw_message.startswith("Value error, "):
        message = raw_message.removeprefix("Value error, ")
    else:
        message = f"Valor inválido para o campo {field}." if field else "Os dados informados são inválidos."
    return JSONResponse(
        status_code=422,
        content={
            "codigo": "ENTRADA_INVALIDA",
            "mensagem": message,
            "campo": field or None,
        },
    )


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, error: HTTPException):
    detail = error.detail if isinstance(error.detail, dict) else {}
    return JSONResponse(
        status_code=error.status_code,
        content={
            "codigo": detail.get("codigo", "ERRO_HTTP"),
            "mensagem": detail.get("mensagem", "Não foi possível processar a solicitação."),
            "campo": detail.get("campo"),
        },
    )


@app.get("/api/v1/nbr10844/health")
def health():
    return {"status": "ok", "norma": "ABNT NBR 10844:1989", "versaoMotor": "0.2.0"}


app.include_router(router, prefix="/api/v1/nbr10844")