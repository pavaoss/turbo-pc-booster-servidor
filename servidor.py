"""
servidor.py  —  Servidor de licença + loja do HyperFPS
--------------------------------------------------------------
Faz tudo do lado do "dono":
- Valida/ativa keys travando 1 key por PC (HWID).
- Painel de ADMIN no navegador (criar, revogar, liberar, listar keys).
- LOJA pública com pagamento PIX (Mercado Pago) que gera a key automaticamente.

Coloque este arquivo na MESMA pasta de licenca.py.

Instalar e rodar:
    pip install fastapi uvicorn
    set ADMIN_TOKEN=meu_token_secreto
    set MP_ACCESS_TOKEN=APP_USR-xxxxx       (token do Mercado Pago p/ a loja)
    python servidor.py

Acesse:
    Loja .......... http://localhost:8000/loja
    Admin ......... http://localhost:8000/admin
"""

import os
import ssl
import json
import uuid
import sqlite3
import smtplib
import urllib.parse
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from fastapi.middleware.cors import CORSMiddleware

try:
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
    from pydantic import BaseModel
    import uvicorn
except ImportError:
    print("Faltam dependências. Rode:  pip install fastapi uvicorn")
    raise SystemExit(1)

from licenca import gerar_key, validar_key

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "Theo20p11")
MP_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "APP_USR-4807753332506280-060416-c6e66e0e1bae55314f87e35ab4e82af4-98365695")
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "licencas.db")

# E-mail (SMTP) para enviar a key ao comprador.
# Gmail: SMTP_HOST=smtp.gmail.com / SMTP_PORT=587 / SMTP_USER=seu@gmail.com /
#        SMTP_PASS = "senha de app" (não a senha normal; gere em myaccount.google.com).
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "turbopcltda@gmail.com")
SMTP_PASS = os.environ.get("SMTP_PASS", "vnok oubc tdhh ivpz")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER or "no-reply@turbopcbooster")

# Download do app: aponte para o .exe local OU para uma URL externa (Drive, etc.)
APP_EXE = os.environ.get("APP_EXE",
                         os.path.join(os.path.dirname(os.path.abspath(__file__)), "HyperFPS.exe"))
DOWNLOAD_URL = os.environ.get("DOWNLOAD_URL", "")

# Suporte/comunidade e endereço público do site (para cartão de crédito)
DISCORD_URL = os.environ.get("DISCORD_URL", "https://discord.gg/bTz4VptTwQ")
WHATSAPP_URL = os.environ.get("WHATSAPP_URL", "")
SITE_URL = os.environ.get("SITE_URL", "https://hyperfps.onrender.com")

# Pushover: notificação no seu celular quando vender.
# Crie conta em pushover.net, pegue seu "User Key" e crie um "Application" p/ o Token.
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "")
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "")

# Planos por NÍVEL de funcionalidade (nivel 1..4). Preços em R$ — edite à vontade.
# preco_de = preço "cheio" riscado (sensação de promoção). Apague p/ não mostrar.
PLANOS = {
    "basico":   {"nome": "Básico",   "nivel": 1, "dias": 30, "preco":  12.00, "preco_de": 19.90,
                 "otimizacoes": 5,  "subtitulo": "Pra dar uma aliviada no PC"},
    "turbo":    {"nome": "Turbo",    "nivel": 2, "dias": 30, "preco": 21.90, "preco_de": 29.90,
                 "otimizacoes": 12, "subtitulo": "Mais FPS nos jogos"},
    "pro":      {"nome": "Pro",      "nivel": 3, "dias": 30, "preco": 36.97, "preco_de": 44.90,
                 "otimizacoes": 18, "subtitulo": "O kit completo de gamer", "destaque": True},
    "ultimate": {"nome": "Ultimate", "nivel": 4, "dias": 30, "preco": 60,99, "preco_de": 82,99,
                 "otimizacoes": 20, "subtitulo": "Tudo, sem limite"},
}

# Lista de recursos mostrada na loja. (texto, nível mínimo que o inclui)
FEATURES = [
    ("Limpeza essencial (temporários, DNS, lixeira)", 1),
    ("Plano de energia + Modo de Jogo", 1),
    ("Desativar Game DVR (mais FPS)", 2),
    ("Prioridade de jogos + ajuste de rede", 2),
    ("Cache de shaders, prefetch e miniaturas", 2),
    ("Fechar apps em 2º plano", 2),
    ("Aceleração de mouse + tweaks avançados", 3),
    ("🔥 Modo Turbo (boost automático no jogo)", 3),
    ("⚙️ Gerenciar inicialização do Windows", 3),
    ("Otimizar disco (TRIM) + desativar telemetria", 4),
    ("Suporte prioritário", 4),
]

app = FastAPI(title="HyperFPS")
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://hyperfps.shop"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def conectar():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS licencas (
        key TEXT PRIMARY KEY, hwid TEXT, revogada INTEGER DEFAULT 0,
        plano TEXT, ativada_em TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS pagamentos (
        pagamento_id TEXT PRIMARY KEY, plano TEXT, key TEXT,
        status TEXT, email TEXT, criado_em TEXT)""")
    try:
        con.execute("ALTER TABLE pagamentos ADD COLUMN email TEXT")  # bancos antigos
    except Exception:
        pass
    con.execute("CREATE TABLE IF NOT EXISTS cupons "
                "(codigo TEXT PRIMARY KEY, percent INTEGER, ativo INTEGER DEFAULT 1)")
    con.execute("CREATE TABLE IF NOT EXISTS depoimentos "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT, texto TEXT, estrelas INTEGER DEFAULT 5)")
    return con


# ====================================================================
# MERCADO PAGO (PIX)
# ====================================================================
def mp_criar_pix(valor, email, descricao):
    if not MP_TOKEN:
        return None, "Loja não configurada (defina MP_ACCESS_TOKEN)."
    body = json.dumps({
        "transaction_amount": round(float(valor), 2),
        "description": descricao,
        "payment_method_id": "pix",
        "payer": {"email": email or "comprador@email.com"},
    }).encode()
    req = urllib.request.Request(
        "https://api.mercadopago.com/v1/payments", data=body,
        headers={"Authorization": f"Bearer {MP_TOKEN}",
                 "Content-Type": "application/json",
                 "X-Idempotency-Key": str(uuid.uuid4())})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r), None
    except Exception as e:
        return None, f"Erro ao falar com o Mercado Pago: {e}"


def mp_status(pagamento_id):
    if not MP_TOKEN:
        return "desconhecido"
    req = urllib.request.Request(
        f"https://api.mercadopago.com/v1/payments/{pagamento_id}",
        headers={"Authorization": f"Bearer {MP_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r).get("status", "desconhecido")
    except Exception:
        return "desconhecido"


def emitir_key(plano):
    info = PLANOS[plano]
    return gerar_key(info["dias"], info.get("nivel", 4))


def enviar_email(destino, key, plano):
    """Envia a key para o e-mail do comprador. Se o SMTP não estiver configurado
    (ou faltar e-mail), apenas avisa no console — a key ainda aparece na tela."""
    if not destino or not SMTP_HOST or not SMTP_USER:
        print("[email] SMTP não configurado ou sem destino; envio pulado.")
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = "Sua key do HyperFPS ⚡"
        msg["From"] = SMTP_FROM
        msg["To"] = destino
        msg.set_content(
            f"Obrigado pela compra do plano {plano}!\n\n"
            f"Sua key de ativacao:\n\n    {key}\n\n"
            f"Abra o HyperFPS e cole a key na tela de ativacao.\n"
            f"Bons jogos!")
        msg.add_alternative(
            f"<div style='font-family:Arial;color:#222'>"
            f"<h2 style='color:#00a152'>⚡ HyperFPS</h2>"
            f"<p>Obrigado pela compra do plano <b>{plano}</b>!</p>"
            f"<p>Sua key de ativação:</p>"
            f"<p style='font-size:20px;background:#f0f0f0;padding:12px;"
            f"border-radius:8px'><b>{key}</b></p>"
            f"<p>Abra o app e cole a key na tela de ativação. Bons jogos! 🚀</p>"
            f"</div>", subtype="html")
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        print(f"[email] key enviada para {destino}")
        return True
    except Exception as e:
        print(f"[email] falha ao enviar para {destino}: {e}")
        return False


def notificar_pushover(titulo, mensagem):
    """Manda uma notificação para o SEU celular (app Pushover)."""
    if not PUSHOVER_TOKEN or not PUSHOVER_USER:
        print("[pushover] não configurado; notificação pulada.")
        return False
    try:
        dados = urllib.parse.urlencode({
            "token": PUSHOVER_TOKEN,
            "user": PUSHOVER_USER,
            "title": titulo,
            "message": mensagem,
            "priority": "1",
            "sound": "cashregister",
        }).encode()
        req = urllib.request.Request("https://api.pushover.net/1/messages.json", data=dados)
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        print(f"[pushover] enviado: {titulo}")
        return True
    except Exception as e:
        print(f"[pushover] falha: {e}")
        return False


# ====================================================================
# MODELOS
# ====================================================================
class Ativacao(BaseModel):
    key: str
    hwid: str


class Compra(BaseModel):
    plano: str
    email: str = ""
    cupom: str = ""


class CriarLote(BaseModel):
    quantidade: int = 1
    dias: int = 30


class KeyRef(BaseModel):
    key: str


class CupomCriar(BaseModel):
    codigo: str
    percent: int = 10


class CupomRef(BaseModel):
    codigo: str


class DepoCriar(BaseModel):
    nome: str
    texto: str
    estrelas: int = 5


class DepoRef(BaseModel):
    id: int


class EmailRef(BaseModel):
    email: str


# ====================================================================
# APP (otimizador) -> ativar / validar
# ====================================================================
@app.post("/ativar")
def ativar(d: Ativacao):
    key = d.key.strip().upper()
    ok, msg = validar_key(key)
    if not ok:
        return {"ok": False, "msg": msg}
    con = conectar()
    row = con.execute("SELECT hwid, revogada FROM licencas WHERE key=?", (key,)).fetchone()
    if row and row[1] == 1:
        con.close(); return {"ok": False, "msg": "Key revogada."}
    if row is None:
        con.execute("INSERT INTO licencas (key, hwid, ativada_em) VALUES (?,?,?)",
                    (key, d.hwid, datetime.now().isoformat()))
        con.commit(); con.close()
        return {"ok": True, "msg": "Key ativada neste PC. " + msg}
    if row[0] == d.hwid:
        con.close(); return {"ok": True, "msg": "Key já ativa neste PC. " + msg}
    con.close()
    return {"ok": False, "msg": "Esta key já está em uso em outro PC."}


@app.post("/validar")
def validar(d: Ativacao):
    key = d.key.strip().upper()
    ok, msg = validar_key(key)
    if not ok:
        return {"ok": False, "msg": msg}
    con = conectar()
    row = con.execute("SELECT hwid, revogada FROM licencas WHERE key=?", (key,)).fetchone()
    con.close()
    if row is None:
        return {"ok": False, "msg": "Key ainda não ativada."}
    if row[1] == 1:
        return {"ok": False, "msg": "Key revogada."}
    if row[0] != d.hwid:
        return {"ok": False, "msg": "Key registrada em outro PC."}
    return {"ok": True, "msg": msg}


# ====================================================================
# LOJA (pública)
# ====================================================================
@app.post("/comprar")
def comprar(d: Compra):
    if d.plano not in PLANOS:
        return {"ok": False, "msg": "Plano inválido."}
    plano = PLANOS[d.plano]

    # Trial é grátis: gera a key na hora
    if plano["preco"] <= 0:
        key = emitir_key(d.plano)
        enviar_email(d.email, key, plano["nome"])
        notificar_pushover("🎁 Novo trial ativado",
                           f"Plano {plano['nome']} (grátis)\nCliente: {d.email or 'sem e-mail'}")
        return {"ok": True, "gratis": True, "key": key}

    # Aplica cupom de desconto (validado no servidor)
    preco = plano["preco"]
    pct = _cupom_percent(d.cupom) if d.cupom else 0
    if pct:
        preco = round(preco * (1 - pct / 100.0), 2)

    desc = f"HyperFPS - {plano['nome']}" + (f" (cupom {d.cupom.strip().upper()} -{pct}%)" if pct else "")
    pago, erro = mp_criar_pix(preco, d.email, desc)
    if erro:
        return {"ok": False, "msg": erro}

    pid = str(pago["id"])
    tdata = pago.get("point_of_interaction", {}).get("transaction_data", {})
    con = conectar()
    con.execute("INSERT OR REPLACE INTO pagamentos "
                "(pagamento_id, plano, key, status, email, criado_em) "
                "VALUES (?,?,?,?,?,?)", (pid, d.plano, "", pago.get("status", "pending"),
                                         d.email, datetime.now().isoformat()))
    con.commit(); con.close()
    return {"ok": True, "gratis": False, "pagamento_id": pid,
            "valor": plano["preco"],
            "qr_code": tdata.get("qr_code", ""),
            "qr_base64": tdata.get("qr_code_base64", "")}


@app.get("/status/{pagamento_id}")
def status_pagamento(pagamento_id: str):
    con = conectar()
    row = con.execute("SELECT plano, key, email FROM pagamentos WHERE pagamento_id=?",
                      (pagamento_id,)).fetchone()
    if not row:
        con.close(); return {"ok": False, "msg": "Pagamento não encontrado."}
    plano, key, email = row
    st = mp_status(pagamento_id)
    if st == "approved":
        if not key:
            key = emitir_key(plano)
            con.execute("UPDATE pagamentos SET key=?, status=? WHERE pagamento_id=?",
                        (key, "approved", pagamento_id))
            con.commit()
            info = PLANOS.get(plano, {})
            nome_plano = info.get("nome", plano)
            preco = info.get("preco", 0)
            enviar_email(email, key, nome_plano)
            notificar_pushover(
                "💰 Compra aprovada!",
                f"Plano {nome_plano} — R$ {preco:.2f}".replace(".", ",")
                + f"\nCliente: {email or 'sem e-mail'}")
        con.close()
        return {"ok": True, "status": "approved", "key": key}
    con.execute("UPDATE pagamentos SET status=? WHERE pagamento_id=?", (st, pagamento_id))
    con.commit(); con.close()
    return {"ok": True, "status": st}


@app.post("/webhook")
def webhook(payload: dict):
    # Mercado Pago notifica aqui quando o pagamento muda de status.
    try:
        pid = str(payload.get("data", {}).get("id", "")) or str(payload.get("id", ""))
        if pid:
            con = conectar()
            row = con.execute("SELECT plano FROM pagamentos WHERE pagamento_id=?", (pid,)).fetchone()
            con.close()
            if row:
                status_pagamento(pid)   # fluxo PIX
            else:
                status_mp(pid)          # fluxo cartão (Checkout Pro)
    except Exception:
        pass
    return {"ok": True}


# ====================================================================
# ADMIN (token)
# ====================================================================
def _admin(token):
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido.")


@app.post("/admin/criar")
def admin_criar(d: CriarLote, x_admin_token: str = Header(default="")):
    _admin(x_admin_token)
    return {"ok": True, "keys": [gerar_key(d.dias) for _ in range(d.quantidade)]}


@app.post("/admin/revogar")
def admin_revogar(d: KeyRef, x_admin_token: str = Header(default="")):
    _admin(x_admin_token)
    con = conectar()
    con.execute("INSERT INTO licencas (key, hwid, revogada) VALUES (?,?,1) "
                "ON CONFLICT(key) DO UPDATE SET revogada=1", (d.key.strip().upper(), ""))
    con.commit(); con.close()
    return {"ok": True}


@app.post("/admin/liberar")
def admin_liberar(d: KeyRef, x_admin_token: str = Header(default="")):
    _admin(x_admin_token)
    con = conectar()
    con.execute("DELETE FROM licencas WHERE key=?", (d.key.strip().upper(),))
    con.commit(); con.close()
    return {"ok": True}


@app.get("/admin/listar")
def admin_listar(x_admin_token: str = Header(default="")):
    _admin(x_admin_token)
    con = conectar()
    rows = con.execute("SELECT key, hwid, revogada, ativada_em FROM licencas "
                       "ORDER BY ativada_em DESC").fetchall()
    con.close()
    return {"ok": True, "licencas": [
        {"key": r[0], "hwid": r[1], "revogada": bool(r[2]), "ativada_em": r[3]} for r in rows]}


# ---------- Cupons ----------
def _cupom_percent(codigo):
    try:
        con = conectar()
        row = con.execute("SELECT percent, ativo FROM cupons WHERE codigo=?",
                          (codigo.strip().upper(),)).fetchone()
        con.close()
        if row and row[1] == 1 and 0 < row[0] < 100:
            return int(row[0])
    except Exception:
        pass
    return 0


@app.get("/cupom/{codigo}")
def ver_cupom(codigo: str):
    pct = _cupom_percent(codigo)
    return {"ok": True, "percent": pct} if pct else {"ok": False, "msg": "Cupom inválido."}


@app.post("/admin/cupom/criar")
def admin_cupom_criar(d: CupomCriar, x_admin_token: str = Header(default="")):
    _admin(x_admin_token)
    con = conectar()
    con.execute("INSERT OR REPLACE INTO cupons (codigo, percent, ativo) VALUES (?,?,1)",
                (d.codigo.strip().upper(), int(d.percent)))
    con.commit(); con.close()
    return {"ok": True}


@app.get("/admin/cupom/listar")
def admin_cupom_listar(x_admin_token: str = Header(default="")):
    _admin(x_admin_token)
    con = conectar()
    rows = con.execute("SELECT codigo, percent, ativo FROM cupons").fetchall()
    con.close()
    return {"ok": True, "cupons": [{"codigo": r[0], "percent": r[1], "ativo": bool(r[2])} for r in rows]}


@app.post("/admin/cupom/remover")
def admin_cupom_remover(d: CupomRef, x_admin_token: str = Header(default="")):
    _admin(x_admin_token)
    con = conectar()
    con.execute("DELETE FROM cupons WHERE codigo=?", (d.codigo.strip().upper(),))
    con.commit(); con.close()
    return {"ok": True}


# ---------- Download do app ----------
@app.get("/baixar")
def baixar():
    if DOWNLOAD_URL:
        return RedirectResponse(DOWNLOAD_URL)
    if os.path.exists(APP_EXE):
        return FileResponse(APP_EXE, filename="HyperFPS.exe",
                            media_type="application/octet-stream")
    return HTMLResponse("<p>Download ainda não disponível.</p>", status_code=404)


# ---------- Cartão de crédito (Mercado Pago Checkout Pro) ----------
def mp_criar_preferencia(valor, email, titulo, extref):
    if not MP_TOKEN:
        return None, "Loja não configurada (MP_ACCESS_TOKEN)."
    body = json.dumps({
        "items": [{"title": titulo, "quantity": 1, "currency_id": "BRL",
                   "unit_price": round(float(valor), 2)}],
        "payer": {"email": email or "comprador@email.com"},
        "external_reference": extref,
        "back_urls": {"success": SITE_URL.rstrip("/") + "/obrigado"},
        "auto_return": "approved",
        "notification_url": SITE_URL.rstrip("/") + "/webhook",
    }).encode()
    req = urllib.request.Request("https://api.mercadopago.com/checkout/preferences", data=body,
                                 headers={"Authorization": f"Bearer {MP_TOKEN}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r), None
    except Exception as e:
        return None, f"Erro Mercado Pago: {e}"


def mp_payment(pid):
    if not MP_TOKEN:
        return {}
    req = urllib.request.Request(f"https://api.mercadopago.com/v1/payments/{pid}",
                                 headers={"Authorization": f"Bearer {MP_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except Exception:
        return {}


def _entregar(pid, plano, email):
    """Emite e entrega a key uma única vez por pagamento (idempotente)."""
    con = conectar()
    row = con.execute("SELECT key FROM pagamentos WHERE pagamento_id=?", (pid,)).fetchone()
    if row and row[0]:
        con.close()
        return row[0]
    key = emitir_key(plano)
    con.execute("INSERT OR REPLACE INTO pagamentos "
                "(pagamento_id, plano, key, status, email, criado_em) VALUES (?,?,?,?,?,?)",
                (pid, plano, key, "approved", email, datetime.now().isoformat()))
    con.commit(); con.close()
    info = PLANOS.get(plano, {})
    enviar_email(email, key, info.get("nome", plano))
    notificar_pushover("💰 Compra aprovada!",
                       ("Plano %s — R$ %.2f" % (info.get("nome", plano), info.get("preco", 0))).replace(".", ",")
                       + f"\nCliente: {email or 'sem e-mail'}")
    return key


@app.post("/comprar-cartao")
def comprar_cartao(d: Compra):
    if d.plano not in PLANOS:
        return {"ok": False, "msg": "Plano inválido."}
    plano = PLANOS[d.plano]
    if plano["preco"] <= 0:
        key = emitir_key(d.plano)
        enviar_email(d.email, key, plano["nome"])
        return {"ok": True, "gratis": True, "key": key}
    preco = plano["preco"]
    pct = _cupom_percent(d.cupom) if d.cupom else 0
    if pct:
        preco = round(preco * (1 - pct / 100.0), 2)
    pref, erro = mp_criar_preferencia(preco, d.email, f"HyperFPS - {plano['nome']}",
                                      f"{d.plano}|{d.email}")
    if erro:
        return {"ok": False, "msg": erro}
    return {"ok": True, "url": pref.get("init_point") or pref.get("sandbox_init_point", "")}


@app.get("/status-mp/{pid}")
def status_mp(pid: str):
    pg = mp_payment(pid)
    st = pg.get("status", "")
    if st == "approved":
        ext = pg.get("external_reference", "") or ""
        plano = ext.split("|")[0] if ext else ""
        email = ext.split("|")[1] if "|" in ext else ""
        if plano in PLANOS:
            return {"ok": True, "status": "approved", "key": _entregar(pid, plano, email)}
    return {"ok": True, "status": st or "pending"}


# ---------- Recuperar / reenviar key ----------
@app.post("/recuperar")
def recuperar(d: EmailRef):
    email = d.email.strip()
    if not email:
        return {"ok": False, "msg": "Informe seu e-mail."}
    con = conectar()
    rows = con.execute("SELECT key FROM pagamentos WHERE email=? AND key!=''", (email,)).fetchall()
    con.close()
    keys = [r[0] for r in rows]
    if not keys:
        return {"ok": False, "msg": "Nenhuma compra encontrada para esse e-mail."}
    for k in keys:
        enviar_email(email, k, "sua compra")
    return {"ok": True, "msg": f"Enviamos {len(keys)} key(s) para o seu e-mail."}


# ---------- Depoimentos (admin) e contagem de vendas ----------
def get_depoimentos():
    try:
        con = conectar()
        rows = con.execute("SELECT nome, texto, estrelas FROM depoimentos ORDER BY id DESC LIMIT 12").fetchall()
        con.close()
        return rows
    except Exception:
        return []


def contar_vendas():
    try:
        con = conectar()
        n = con.execute("SELECT COUNT(*) FROM licencas WHERE revogada=0").fetchone()[0]
        con.close()
        return n
    except Exception:
        return 0


@app.post("/admin/depo/criar")
def admin_depo_criar(d: DepoCriar, x_admin_token: str = Header(default="")):
    _admin(x_admin_token)
    con = conectar()
    con.execute("INSERT INTO depoimentos (nome, texto, estrelas) VALUES (?,?,?)",
                (d.nome, d.texto, max(1, min(5, int(d.estrelas)))))
    con.commit(); con.close()
    return {"ok": True}


@app.get("/admin/depo/listar")
def admin_depo_listar(x_admin_token: str = Header(default="")):
    _admin(x_admin_token)
    con = conectar()
    rows = con.execute("SELECT id, nome, texto, estrelas FROM depoimentos ORDER BY id DESC").fetchall()
    con.close()
    return {"ok": True, "depoimentos": [{"id": r[0], "nome": r[1], "texto": r[2], "estrelas": r[3]} for r in rows]}


@app.post("/admin/depo/remover")
def admin_depo_remover(d: DepoRef, x_admin_token: str = Header(default="")):
    _admin(x_admin_token)
    con = conectar()
    con.execute("DELETE FROM depoimentos WHERE id=?", (int(d.id),))
    con.commit(); con.close()
    return {"ok": True}


# ---------- Lembrete de vencimento ----------
def _expiry_da_key(key):
    try:
        return datetime.strptime(key.strip().upper().split("-")[3], "%Y%m%d").date()
    except Exception:
        return None


@app.get("/admin/vencimentos")
def admin_vencimentos(dias: int = 3, x_admin_token: str = Header(default="")):
    _admin(x_admin_token)
    con = conectar()
    rows = con.execute("SELECT l.key, p.email FROM licencas l "
                       "LEFT JOIN pagamentos p ON p.key=l.key WHERE l.revogada=0").fetchall()
    con.close()
    hoje = datetime.now().date()
    avisados = 0
    for key, email in rows:
        exp = _expiry_da_key(key)
        if exp and email and 0 <= (exp - hoje).days <= dias:
            enviar_email(email, key, "renovação (sua key vai expirar)")
            avisados += 1
    return {"ok": True, "avisados": avisados}


# ====================================================================
# PÁGINAS HTML
# ====================================================================
ESTILO = """
<style>
 *{box-sizing:border-box}
 body{font-family:Arial,Helvetica,sans-serif;background:#0e0f13;color:#e8eaed;margin:0;padding:30px}
 .card{background:#1b1e27;border:1px solid #2a2f3c;border-radius:16px;padding:24px;max-width:1040px;margin:14px auto}
 h1{color:#00e676;margin:0} h2{margin-top:0}
 button{background:#00e676;color:#06210f;border:0;border-radius:10px;padding:12px 16px;font-weight:bold;cursor:pointer;width:100%}
 button:hover{filter:brightness(1.08)}
 button.alt{background:#22262f;color:#1de9ff;width:auto} button.danger{background:#ff5252;color:#fff;width:auto}
 input,select{background:#0e0f13;color:#e8eaed;border:1px solid #2a2f3c;border-radius:8px;padding:10px;width:100%;margin:6px 0}
 table{width:100%;border-collapse:collapse;font-size:13px} td,th{border-bottom:1px solid #2a2f3c;padding:8px;text-align:left}
 code{background:#0e0f13;padding:6px 10px;border-radius:6px;color:#00e676;word-break:break-all;display:inline-block}
 .muted{color:#8b909a;font-size:12px}
 .grade{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-top:20px}
 @media(max-width:880px){.grade{grid-template-columns:1fr 1fr}}
 @media(max-width:520px){.grade{grid-template-columns:1fr}}
 .plano{position:relative;background:#13151c;border:1px solid #2a2f3c;border-radius:16px;padding:22px 18px;display:flex;flex-direction:column}
 .plano.destaque{border:2px solid #00e676;box-shadow:0 0 28px rgba(0,230,118,.18);transform:translateY(-6px)}
 .plano h3{margin:0 0 2px;font-size:22px}
 .sub{color:#8b909a;font-size:12px;min-height:30px;margin:0 0 8px}
 .preco{font-size:30px;font-weight:900;color:#fff}
 .periodo{color:#8b909a;font-size:12px;margin-bottom:14px}
 .plano ul{list-style:none;padding:0;margin:0 0 16px;flex:1}
 .plano li{font-size:12.5px;padding:5px 0;border-bottom:1px solid #20242e}
 .plano li.ok{color:#e8eaed} .plano li.no{color:#555b66;text-decoration:line-through}
 .badge{position:absolute;top:-12px;left:50%;transform:translateX(-50%);background:#00e676;color:#06210f;font-size:11px;font-weight:bold;padding:4px 12px;border-radius:20px;white-space:nowrap}
</style>"""


@app.get("/", response_class=HTMLResponse)
def raiz():
    return f"{ESTILO}<div class='card'><h1>⚡ HyperFPS</h1>" \
           "<p><a style='color:#1de9ff' href='/loja'>Ir para a Loja</a> &nbsp;|&nbsp; " \
           "<a style='color:#1de9ff' href='/download'>Baixar o app</a> &nbsp;|&nbsp; " \
           "<a style='color:#1de9ff' href='/admin'>Painel Admin</a></p></div>"


ESTILO_LOJA = """
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;800&display=swap" rel="stylesheet">
<style>
 *{box-sizing:border-box;margin:0;padding:0}
 body{font-family:'Poppins',Arial,sans-serif;color:#e8eaed;min-height:100vh;padding:46px 18px;
   background:radial-gradient(1100px 560px at 50% -8%,#10271c 0%,#0e0f13 58%)}
 .hero{text-align:center;margin-bottom:6px}
 .logo{font-size:48px;line-height:1;filter:drop-shadow(0 0 20px rgba(0,230,118,.65))}
 .hero h1{font-size:clamp(26px,5vw,36px);font-weight:800;letter-spacing:1px;margin-top:6px;
   background:linear-gradient(90deg,#00e676,#1de9ff);-webkit-background-clip:text;background-clip:text;color:transparent}
 .hero .tag{color:#9aa0aa;font-size:14px;margin-top:8px}
 .emailbox{max-width:430px;margin:24px auto 0;background:#15171e;border:1px solid #2a2f3c;border-radius:14px;padding:16px 18px;text-align:left}
 .emailbox label{font-size:12px;color:#8b909a}
 input{width:100%;background:#0e0f13;color:#e8eaed;border:1px solid #2a2f3c;border-radius:10px;padding:12px;font-size:14px;margin-top:6px;font-family:inherit}
 input:focus{outline:none;border-color:#00e676;box-shadow:0 0 0 3px rgba(0,230,118,.16)}
 .grade{display:grid;grid-template-columns:repeat(4,1fr);gap:18px;max-width:1120px;margin:34px auto 0}
 @media(max-width:920px){.grade{grid-template-columns:1fr 1fr}}
 @media(max-width:540px){.grade{grid-template-columns:1fr}}
 .plano{position:relative;background:#14161d;border:1px solid #262b37;border-radius:18px;padding:26px 20px;
   display:flex;flex-direction:column;transition:transform .18s,border-color .18s,box-shadow .18s}
 .plano:hover{transform:translateY(-7px);border-color:#3a4150}
 .plano.destaque{border:2px solid #00e676;box-shadow:0 0 42px rgba(0,230,118,.20)}
 .ribbon{position:absolute;top:-13px;left:50%;transform:translateX(-50%);
   background:linear-gradient(90deg,#00e676,#00c853);color:#06210f;font-size:11px;font-weight:600;
   padding:6px 16px;border-radius:20px;white-space:nowrap;box-shadow:0 6px 16px rgba(0,230,118,.45)}
 .otim{align-self:flex-start;background:rgba(0,230,118,.12);color:#00e676;font-size:11px;font-weight:600;
   padding:5px 11px;border-radius:20px;margin-bottom:14px}
 .plano h3{font-size:22px;font-weight:600}
 .sub{color:#8b909a;font-size:12.5px;min-height:34px;margin:3px 0 14px}
 .de{color:#5f6571;font-size:14px;text-decoration:line-through;margin-right:7px}
 .pr{font-size:32px;font-weight:800}
 .per{color:#8b909a;font-size:12px}
 .economize{display:inline-block;background:rgba(29,233,255,.12);color:#1de9ff;font-size:11px;font-weight:600;
   padding:3px 9px;border-radius:8px;margin-top:8px}
 .plano ul{list-style:none;margin:16px 0;flex:1}
 .plano li{font-size:12.5px;padding:7px 0;border-bottom:1px solid #20242e;display:flex;gap:8px;line-height:1.4}
 .plano li.ok{color:#dfe3e8} .plano li.no{color:#565c67}
 .plano li.no span{text-decoration:line-through}
 .ic{flex-shrink:0;font-weight:700} .ok .ic{color:#00e676} .no .ic{color:#41475360}
 button{width:100%;border:0;border-radius:12px;padding:14px;font-weight:600;font-size:15px;cursor:pointer;
   font-family:inherit;transition:.15s;background:linear-gradient(90deg,#00e676,#00c853);color:#06210f}
 button:hover{filter:brightness(1.08);box-shadow:0 8px 22px rgba(0,230,118,.38)}
 .plano:not(.destaque) button{background:#222734;color:#00e676}
 .plano:not(.destaque) button:hover{background:#2a3140;box-shadow:none}
 .rodape{text-align:center;color:#8b909a;font-size:12px;margin:30px auto 0}
 #area{max-width:520px;margin:24px auto 0;background:#14161d;border:1px solid #262b37;border-radius:16px;padding:26px;text-align:center}
 #area h2{margin-bottom:10px} code{background:#0e0f13;padding:11px;border-radius:8px;color:#00e676;word-break:break-all;display:block;margin-top:8px;font-size:13px}
 #carrinho{max-width:540px;margin:26px auto 0;background:#14161d;border:1px solid #262b37;border-radius:16px;padding:22px}
 #carrinho h2{font-size:18px;margin-bottom:14px}
 .item{display:flex;justify-content:space-between;align-items:center;background:#0e0f13;border:1px solid #20242e;border-radius:12px;padding:14px 16px;margin-bottom:10px}
 .item .muted{margin-top:3px}
 .ip{font-weight:700;font-size:18px;display:flex;align-items:center;gap:12px}
 .rm{color:#ff5252;cursor:pointer;font-size:13px;border:1px solid #3a2226;border-radius:8px;padding:3px 8px}
 .rm:hover{background:#241419}
 .total{text-align:right;font-size:16px;margin:6px 0 16px;color:#dfe3e8}
 #carrinho label{font-size:12px;color:#8b909a;display:block;margin:0 0 2px}
 .cupom{display:flex;gap:8px;margin:4px 0 12px}
 .cupom input{margin:0} .cupom button{width:auto;padding:10px 16px;font-size:13px}
 .cupom .ap{background:#222734;color:#1de9ff}
 .okcupom{color:#00e676;font-size:12px;margin-bottom:8px}
 .garantia{max-width:1120px;margin:18px auto 0;text-align:center;color:#cfd3da;font-size:13px;
   background:rgba(0,230,118,.07);border:1px solid rgba(0,230,118,.25);border-radius:14px;padding:12px}
 .faq{max-width:760px;margin:34px auto 0}
 .faq h2{text-align:center;margin-bottom:16px;font-size:22px}
 details{background:#14161d;border:1px solid #262b37;border-radius:12px;padding:14px 18px;margin-bottom:10px}
 details summary{cursor:pointer;font-weight:600;font-size:14px;list-style:none}
 details summary::-webkit-details-marker{display:none}
 details summary::before{content:'+ ';color:#00e676}
 details[open] summary::before{content:'– '}
 details p{color:#9aa0aa;font-size:13px;margin-top:10px;line-height:1.5}
 .dl{display:inline-block;background:linear-gradient(90deg,#00e676,#00c853);color:#06210f;text-decoration:none;
   font-weight:700;font-size:17px;padding:16px 34px;border-radius:14px;box-shadow:0 8px 24px rgba(0,230,118,.35)}
 .dl.off{background:#222734;color:#8b909a;box-shadow:none}
 .passos{max-width:560px;margin:26px auto 0;list-style:none;padding:0}
 .passos li{background:#14161d;border:1px solid #262b37;border-radius:12px;padding:14px 18px;margin-bottom:10px;font-size:14px}
 .passos b{color:#00e676}
 .nav{text-align:center;margin-bottom:8px}
 .nav a{color:#1de9ff;text-decoration:none;font-size:13px;margin:0 8px}
 .selos{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;max-width:900px;margin:16px auto 0}
 .selo{background:#14161d;border:1px solid #262b37;border-radius:20px;padding:8px 16px;font-size:12px;color:#cfd3da}
 .selo b{color:#00e676}
 .beneficios{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;max-width:900px;margin:26px auto 0}
 @media(max-width:680px){.beneficios{grid-template-columns:1fr}}
 .ben{background:#14161d;border:1px solid #262b37;border-radius:14px;padding:18px;text-align:center}
 .ben .e{font-size:26px} .ben h4{margin:8px 0 4px;font-size:15px} .ben p{color:#9aa0aa;font-size:12.5px;line-height:1.4}
 .depos{max-width:1000px;margin:34px auto 0}
 .depos h2{text-align:center;font-size:22px;margin-bottom:16px}
 .depogrid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
 @media(max-width:760px){.depogrid{grid-template-columns:1fr}}
 .depo{background:#14161d;border:1px solid #262b37;border-radius:14px;padding:18px}
 .depo .est{color:#ffc107;font-size:14px} .depo p{font-size:13px;color:#dfe3e8;margin:8px 0;line-height:1.5}
 .depo .nm{color:#8b909a;font-size:12px}
 .legal{text-align:center;margin:34px auto 10px;color:#5f6571;font-size:12px}
 .legal a{color:#8b909a;text-decoration:none;margin:0 6px}
 .float{position:fixed;right:18px;bottom:18px;background:linear-gradient(90deg,#00e676,#00c853);color:#06210f;
   text-decoration:none;font-weight:700;font-size:14px;padding:13px 18px;border-radius:30px;
   box-shadow:0 8px 24px rgba(0,0,0,.4);z-index:50}
 button.cartao{background:#222734;color:#1de9ff;margin-top:8px}
 .muted{color:#8b909a;font-size:12px}
</style>"""


def _card_plano(pid, p):
    ribbon = "<div class='ribbon'>⭐ MAIS POPULAR</div>" if p.get("destaque") else ""
    classe = "plano destaque" if p.get("destaque") else "plano"
    preco = "Grátis" if p["preco"] <= 0 else ("R$ %.2f" % p["preco"]).replace(".", ",")
    de = economize = ""
    if p.get("preco_de", 0) > p["preco"] > 0:
        de = "<span class='de'>" + ("R$ %.2f" % p["preco_de"]).replace(".", ",") + "</span>"
        pct = round((1 - p["preco"] / p["preco_de"]) * 100)
        economize = f"<div class='economize'>Economize {pct}%</div>"
    itens = ""
    for texto, nivelmin in FEATURES:
        ok = p["nivel"] >= nivelmin
        itens += (f"<li class='{'ok' if ok else 'no'}'>"
                  f"<span class='ic'>{'✓' if ok else '✗'}</span><span>{texto}</span></li>")
    return (f"<div class='{classe}'>{ribbon}"
            f"<div class='otim'>{p.get('otimizacoes','')} otimizações</div>"
            f"<h3>{p['nome']}</h3><p class='sub'>{p.get('subtitulo','')}</p>"
            f"<div class='precobox'>{de}<span class='pr'>{preco}</span> "
            f"<span class='per'>/{p['dias']} dias</span></div>{economize}"
            f"<ul>{itens}</ul>"
            f"<button onclick=\"adicionar('{pid}')\">Obter {p['nome']}</button></div>")


@app.get("/loja", response_class=HTMLResponse)
def loja():
    cards = "".join(_card_plano(pid, p) for pid, p in PLANOS.items())
    planos_js = "{" + ",".join(
        "'%s':{nome:%s,dias:%d,otim:%d,preco:%s}" % (
            pid, json.dumps(p["nome"]), p["dias"], p.get("otimizacoes", 0),
            json.dumps(("R$ %.2f" % p["preco"]).replace(".", ",")))
        for pid, p in PLANOS.items()) + "}"
    vendas = contar_vendas()
    contador = f"<div class='selo'><b>{vendas}</b> PCs otimizados</div>" if vendas > 0 else ""
    selos = ("<div class='selos'>"
             "<div class='selo'>⚡ Ativação na hora</div>"
             "<div class='selo'>🔒 Pagamento via Mercado Pago</div>"
             "<div class='selo'>🔄 100% reversível</div>" + contador + "</div>")
    benef = ("<div class='beneficios'>"
             "<div class='ben'><div class='e'>🚀</div><h4>Mais FPS</h4><p>Libera CPU e RAM e fecha o que pesa pra sobrar força pro jogo.</p></div>"
             "<div class='ben'><div class='e'>🛡️</div><h4>Seguro</h4><p>Ajustes padrão do Windows, reversíveis e com ponto de restauração.</p></div>"
             "<div class='ben'><div class='e'>🎮</div><h4>Feito pra games</h4><p>Perfis prontos pra FiveM, GTA, CS2 e Modo Turbo automático.</p></div>"
             "</div>")
    depo_html = ""
    depos = get_depoimentos()
    if depos:
        cartoes = ""
        for nome, texto, est in depos:
            e = int(est or 5)
            estrelas = "★" * e + "☆" * (5 - e)
            cartoes += (f"<div class='depo'><div class='est'>{estrelas}</div>"
                        f"<p>\u201c{texto}\u201d</p><div class='nm'>— {nome}</div></div>")
        depo_html = f"<div class='depos'><h2>Quem usa, aprova</h2><div class='depogrid'>{cartoes}</div></div>"
    float_html = ""
    if DISCORD_URL:
        float_html = f"<a class='float' href='{DISCORD_URL}' target='_blank'>💬 Suporte no Discord</a>"
    elif WHATSAPP_URL:
        float_html = f"<a class='float' href='{WHATSAPP_URL}' target='_blank'>💬 Suporte no WhatsApp</a>"
    return f"""{ESTILO_LOJA}
<div class='nav'><a href='/loja'>Planos</a> · <a href='/download'>Baixar o app</a></div>
<div class='hero'>
  <div class='logo'>⚡</div>
  <h1>HYPERFPS</h1>
  <p class='tag'>Deixe seu PC no máximo para FiveM, GTA, CS2 e outros jogos</p>
</div>
<div class='garantia'>🛡️ Garantia de 7 dias — não curtiu, a gente devolve seu dinheiro · ⚡ Entrega na hora</div>
{selos}
{benef}
<div class='grade'>{cards}</div>
<p class='rodape'>🔒 Pagamento seguro via PIX ou cartão · Entrega automática por e-mail · Ative em 1 PC</p>
<div id='carrinho' style='display:none'>
  <h2>🛒 Seu carrinho</h2>
  <div id='itens'></div>
  <label>Cupom de desconto</label>
  <div class='cupom'>
    <input id='cupom' placeholder='Ex.: GAMER10'>
    <button class='ap' onclick='aplicarCupom()'>Aplicar</button>
  </div>
  <div id='okcupom' class='okcupom'></div>
  <div id='total' class='total'></div>
  <label>Seu e-mail (a key será enviada para ele)</label>
  <input id='email' placeholder='voce@email.com'>
  <button onclick='finalizar()'>Finalizar com PIX →</button>
  <button class='cartao' onclick='comprarCartao()'>Pagar com cartão →</button>
  <div id='area' style='display:none;margin-top:18px'></div>
</div>
{depo_html}
<div class='faq'>
  <h2>Perguntas frequentes</h2>
  <details><summary>É seguro? vai estragar meu Windows?</summary>
   <p>Sim, é seguro. São ajustes padrão do Windows, todos reversíveis. O app cria um ponto de restauração antes de otimizar e tem o botão "Restaurar padrões" pra desfazer tudo.</p></details>
  <details><summary>Funciona no meu PC?</summary>
   <p>Funciona no Windows 10 e 11. O monitor de GPU em tempo real precisa de placa NVIDIA; o resto roda em qualquer PC.</p></details>
  <details><summary>Vai aumentar meu FPS de verdade?</summary>
   <p>O app libera recursos do PC (fecha o que pesa, ajusta energia, prioridade e limpezas), o que ajuda principalmente em PCs médio/fraco. Não prometemos número mágico de FPS — isso depende também do seu hardware e das configurações do jogo.</p></details>
  <details><summary>Como recebo minha key?</summary>
   <p>Assim que o pagamento é aprovado, a key aparece na tela e também é enviada para o seu e-mail.</p></details>
  <details><summary>Posso usar em mais de um PC?</summary>
   <p>Cada key trava em 1 PC. Se você trocar de computador, é só falar com o suporte que liberamos a key pra ativar no novo.</p></details>
</div>
<div class='legal'><a href='/termos'>Termos de uso</a> · <a href='/reembolso'>Reembolso</a> · <a href='/privacidade'>Privacidade</a></div>
{float_html}
<script>
const PLANOS={planos_js};
let escolhido=null, cupomPct=0;
function precoNum(s){{return parseFloat(s.replace('R$','').replace('.','').replace(',','.').trim());}}
function fmt(n){{return 'R$ '+n.toFixed(2).replace('.',',');}}
function atualizarTotal(){{
  const base=precoNum(PLANOS[escolhido].preco);
  const fim=cupomPct?base*(1-cupomPct/100):base;
  document.getElementById('total').innerHTML="Total: <b>"+fmt(fim)+"</b>"+
    (cupomPct?" <span class='muted'>("+cupomPct+"% off)</span>":"");
}}
function adicionar(pid){{
  escolhido=pid; cupomPct=0;
  const p=PLANOS[pid];
  document.getElementById('itens').innerHTML=
    "<div class='item'><div><b>Plano "+p.nome+"</b>"+
    "<div class='muted'>"+p.dias+" dias · "+p.otim+" otimizações</div></div>"+
    "<div class='ip'>"+p.preco+"<span class='rm' onclick='remover()'>✕ remover</span></div></div>";
  document.getElementById('okcupom').textContent='';
  document.getElementById('cupom').value='';
  atualizarTotal();
  document.getElementById('area').style.display='none';
  const c=document.getElementById('carrinho'); c.style.display='block';
  c.scrollIntoView({{behavior:'smooth'}});
}}
function remover(){{
  escolhido=null; cupomPct=0;
  document.getElementById('carrinho').style.display='none';
  window.scrollTo({{top:0,behavior:'smooth'}});
}}
async function aplicarCupom(){{
  const code=document.getElementById('cupom').value.trim();
  const el=document.getElementById('okcupom');
  if(!code){{return;}}
  const s=await (await fetch('/cupom/'+encodeURIComponent(code))).json();
  if(s.ok){{cupomPct=s.percent; el.style.color='#00e676'; el.textContent='✓ Cupom aplicado: '+s.percent+'% de desconto';}}
  else{{cupomPct=0; el.style.color='#ff5252'; el.textContent='✗ Cupom inválido';}}
  atualizarTotal();
}}
function dadosCarrinho(){{
  return {{plano:escolhido, email:document.getElementById('email').value, cupom:document.getElementById('cupom').value.trim()}};
}}
async function finalizar(){{
  if(!escolhido){{return;}}
  const area=document.getElementById('area'); area.style.display='block'; area.innerHTML='Gerando PIX...';
  const r=await fetch('/comprar',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(dadosCarrinho())}});
  const d=await r.json();
  if(!d.ok){{area.innerHTML='<b>'+(d.msg||'Erro')+'</b>';return;}}
  if(d.gratis){{mostrarKey(d.key);return;}}
  area.innerHTML="<h2>Pague com PIX</h2>"+
    "<img style='width:220px;border-radius:10px' src='data:image/png;base64,"+d.qr_base64+"'>"+
    "<p class='muted' style='margin-top:10px'>Ou copie e cole:</p><code>"+d.qr_code+"</code>"+
    "<p class='muted' style='margin-top:10px'>Assim que o pagamento cair, sua key aparece aqui.</p>"+
    "<p id='st'>⏳ Aguardando pagamento...</p>";
  const t=setInterval(async()=>{{
    const s=await (await fetch('/status/'+d.pagamento_id)).json();
    if(s.status==='approved'){{clearInterval(t);mostrarKey(s.key);}}
  }},4000);
}}
async function comprarCartao(){{
  if(!escolhido){{return;}}
  const area=document.getElementById('area'); area.style.display='block'; area.innerHTML='Redirecionando para o cartão...';
  const r=await fetch('/comprar-cartao',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(dadosCarrinho())}});
  const d=await r.json();
  if(d.ok&&d.gratis){{mostrarKey(d.key);return;}}
  if(d.ok&&d.url){{window.location.href=d.url;return;}}
  area.innerHTML='<b>'+(d.msg||'Erro')+'</b>';
}}
function mostrarKey(k){{
  document.getElementById('area').innerHTML=
   "<h2>✅ Pronto!</h2><p class='muted'>Sua key de ativação:</p><code>"+k+"</code>"+
   "<p class='muted' style='margin-top:10px'>Enviamos também para o seu e-mail. "+
   "<a href='/download' style='color:#1de9ff'>Baixe o app aqui</a> e cole a key.</p>";
}}
</script>"""


@app.get("/download", response_class=HTMLResponse)
def download_page():
    disponivel = bool(DOWNLOAD_URL) or os.path.exists(APP_EXE)
    botao = ("<a class='dl' href='/baixar'>⬇ Baixar para Windows</a>" if disponivel
             else "<span class='dl off'>Download em breve</span>")
    return f"""{ESTILO_LOJA}
<div class='nav'><a href='/loja'>Planos</a> · <a href='/download'>Baixar o app</a></div>
<div class='hero'>
  <div class='logo'>⚡</div>
  <h1>BAIXAR O APP</h1>
  <p class='tag'>Já tem sua key? Baixe o HyperFPS e ative</p>
</div>
<div style='text-align:center;margin-top:24px'>{botao}</div>
<ol class='passos'>
  <li><b>1.</b> Baixe o arquivo e abra com o botão direito → <b>Executar como administrador</b>.</li>
  <li><b>2.</b> Na primeira vez, cole a <b>key</b> que você recebeu por e-mail e clique em Ativar.</li>
  <li><b>3.</b> Escolha um perfil (CS2, FiveM, GTA) ou clique em <b>Detectar</b>.</li>
  <li><b>4.</b> Clique em <b>OTIMIZAR</b> e bons jogos! 🚀</li>
</ol>
<p class='muted' style='text-align:center;margin-top:18px'>
  O Windows pode mostrar um aviso de "app desconhecido" — é normal em programas novos; clique em "Mais informações → Executar assim mesmo".
</p>
<p style='text-align:center;margin-top:16px'><a href='/loja' style='color:#1de9ff'>Ainda não tem key? Ver planos</a></p>"""


@app.get("/obrigado", response_class=HTMLResponse)
def obrigado():
    return f"""{ESTILO_LOJA}
<div class='nav'><a href='/loja'>Planos</a> · <a href='/download'>Baixar o app</a></div>
<div class='hero'><div class='logo'>⚡</div><h1>OBRIGADO!</h1>
<p class='tag'>Estamos confirmando seu pagamento...</p></div>
<div id='area' style='display:block;max-width:520px;margin:24px auto 0;background:#14161d;border:1px solid #262b37;border-radius:16px;padding:26px;text-align:center'>
  <p id='st'>⏳ Verificando pagamento...</p>
</div>
<script>
const params=new URLSearchParams(location.search);
const pid=params.get('payment_id')||params.get('collection_id');
async function checar(){{
  if(!pid){{document.getElementById('st').textContent='Pagamento não identificado. Verifique seu e-mail.';return;}}
  const s=await (await fetch('/status-mp/'+pid)).json();
  if(s.status==='approved'){{
    document.getElementById('area').innerHTML="<h2>✅ Pagamento aprovado!</h2>"+
      "<p class='muted'>Sua key:</p><code style='background:#0e0f13;padding:11px;border-radius:8px;color:#00e676;display:block;margin-top:8px'>"+s.key+"</code>"+
      "<p class='muted' style='margin-top:10px'>Também enviamos para o seu e-mail. <a href='/download' style='color:#1de9ff'>Baixe o app</a>.</p>";
  }} else {{ setTimeout(checar,4000); }}
}}
checar();
</script>"""


def _pagina_texto(titulo, corpo):
    return f"""{ESTILO_LOJA}
<div class='nav'><a href='/loja'>Planos</a> · <a href='/download'>Baixar o app</a></div>
<div class='card' style='max-width:760px;margin:30px auto;background:#14161d;border:1px solid #262b37;border-radius:16px;padding:28px'>
<h1 style='color:#00e676'>{titulo}</h1>{corpo}
<p style='margin-top:20px'><a href='/loja' style='color:#1de9ff'>← Voltar para a loja</a></p></div>"""


@app.get("/recuperar", response_class=HTMLResponse)
def recuperar_page():
    return f"""{ESTILO_LOJA}
<div class='nav'><a href='/loja'>Planos</a> · <a href='/download'>Baixar o app</a></div>
<div class='card' style='max-width:460px;margin:40px auto;background:#14161d;border:1px solid #262b37;border-radius:16px;padding:28px'>
<h1 style='color:#00e676'>Recuperar minha key</h1>
<p class='muted'>Digite o e-mail usado na compra e reenviaremos sua(s) key(s).</p>
<input id='email' placeholder='voce@email.com' style='margin-top:10px'>
<button onclick='rec()' style='margin-top:10px'>Reenviar key</button>
<p id='msg' style='margin-top:12px'></p></div>
<script>
async function rec(){{
  const email=document.getElementById('email').value;
  const r=await fetch('/recuperar',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{email}})}});
  const d=await r.json();
  const m=document.getElementById('msg');
  m.style.color=d.ok?'#00e676':'#ff5252'; m.textContent=(d.ok?'✓ ':'✗ ')+d.msg;
}}
</script>"""


@app.get("/termos", response_class=HTMLResponse)
def termos():
    return _pagina_texto("Termos de uso",
        "<p class='muted'>Modelo — revise com um profissional antes de publicar.</p>"
        "<p>O HyperFPS é um software de otimização para Windows. A licença (key) é pessoal, "
        "vinculada a 1 computador e válida pelo período do plano adquirido. O uso é por conta e risco do "
        "usuário; as alterações são padrão do Windows e reversíveis pelo próprio app. É proibido revender "
        "ou distribuir a key sem autorização.</p>")


@app.get("/reembolso", response_class=HTMLResponse)
def reembolso():
    return _pagina_texto("Política de reembolso",
        "<p class='muted'>Modelo — ajuste às suas regras.</p>"
        "<p>Você tem até <b>7 dias</b> após a compra para solicitar reembolso, caso não fique satisfeito. "
        "O valor é devolvido pelo mesmo meio de pagamento (PIX ou cartão). Para solicitar, entre em contato "
        "pelo nosso suporte informando o e-mail da compra.</p>")


@app.get("/privacidade", response_class=HTMLResponse)
def privacidade():
    return _pagina_texto("Política de privacidade",
        "<p class='muted'>Modelo — adeque à LGPD.</p>"
        "<p>Coletamos apenas o e-mail (para entregar a key e dar suporte) e um identificador do PC (HWID, "
        "para travar a licença em 1 máquina). Não vendemos seus dados. Você pode pedir a exclusão dos seus "
        "dados a qualquer momento pelo suporte.</p>")


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return f"""{ESTILO}
<div class='card'><h1>🔐 Painel Admin</h1>
  <input id='tok' type='password' placeholder='ADMIN_TOKEN'>
  <button onclick='entrar()'>Entrar</button>
</div>
<div class='card' id='painel' style='display:none'>
  <h2>Criar keys</h2>
  <input id='qtd' type='number' value='1' placeholder='Quantidade'>
  <input id='dias' type='number' value='30' placeholder='Dias de validade'>
  <button onclick='criar()'>Gerar</button>
  <div id='novas'></div>
  <h2 style='margin-top:24px'>Licenças</h2>
  <button class='alt' onclick='listar()'>Atualizar lista</button>
  <div id='lista'></div>
  <h2 style='margin-top:24px'>Cupons de desconto</h2>
  <input id='cupcod' placeholder='CÓDIGO (ex.: GAMER10)'>
  <input id='cuppct' type='number' value='10' placeholder='% de desconto'>
  <button onclick='criarCupom()'>Criar cupom</button>
  <button class='alt' onclick='listarCupons()'>Atualizar cupons</button>
  <div id='listacup'></div>
  <h2 style='margin-top:24px'>Depoimentos</h2>
  <input id='depnome' placeholder='Nome do cliente'>
  <input id='deptxt' placeholder='Depoimento'>
  <input id='depest' type='number' value='5' min='1' max='5' placeholder='Estrelas (1-5)'>
  <button onclick='criarDepo()'>Adicionar depoimento</button>
  <div id='listadep'></div>
</div>
<script>
let TOK='';
const H=()=>({{'Content-Type':'application/json','X-Admin-Token':TOK}});
function entrar(){{TOK=document.getElementById('tok').value;
  document.getElementById('painel').style.display='block';listar();listarCupons();listarDepo();}}
async function criar(){{
  const quantidade=+document.getElementById('qtd').value;
  const dias=+document.getElementById('dias').value;
  const r=await fetch('/admin/criar',{{method:'POST',headers:H(),
    body:JSON.stringify({{quantidade,dias}})}});
  const d=await r.json();
  document.getElementById('novas').innerHTML=(d.keys||[]).map(k=>'<code>'+k+'</code>').join('<br>');
}}
async function listar(){{
  const r=await fetch('/admin/listar',{{headers:H()}});
  if(r.status===401){{alert('Token inválido');return;}}
  const d=await r.json();
  let h='<table><tr><th>Key</th><th>PC</th><th>Status</th><th></th></tr>';
  for(const l of d.licencas){{
    h+='<tr><td><code>'+l.key+'</code></td><td class=muted>'+(l.hwid||'-').slice(0,12)+'</td>'+
       '<td>'+(l.revogada?'❌ revogada':'✅ ativa')+'</td>'+
       '<td><button class=danger onclick="rev(\\''+l.key+'\\')">Revogar</button> '+
       '<button class=alt onclick="lib(\\''+l.key+'\\')">Liberar PC</button></td></tr>';
  }}
  document.getElementById('lista').innerHTML=h+'</table>';
}}
async function rev(k){{await fetch('/admin/revogar',{{method:'POST',headers:H(),body:JSON.stringify({{key:k}})}});listar();}}
async function lib(k){{await fetch('/admin/liberar',{{method:'POST',headers:H(),body:JSON.stringify({{key:k}})}});listar();}}
async function criarCupom(){{
  const codigo=document.getElementById('cupcod').value;
  const percent=+document.getElementById('cuppct').value;
  await fetch('/admin/cupom/criar',{{method:'POST',headers:H(),body:JSON.stringify({{codigo,percent}})}});
  listarCupons();
}}
async function listarCupons(){{
  const r=await fetch('/admin/cupom/listar',{{headers:H()}});
  const d=await r.json();
  let h='<table><tr><th>Código</th><th>Desconto</th><th></th></tr>';
  for(const c of (d.cupons||[])){{
    h+='<tr><td><code>'+c.codigo+'</code></td><td>'+c.percent+'%</td>'+
       '<td><button class=danger onclick="delCupom(\\''+c.codigo+'\\')">Excluir</button></td></tr>';
  }}
  document.getElementById('listacup').innerHTML=h+'</table>';
}}
async function delCupom(c){{await fetch('/admin/cupom/remover',{{method:'POST',headers:H(),body:JSON.stringify({{codigo:c}})}});listarCupons();}}
async function criarDepo(){{
  const nome=document.getElementById('depnome').value;
  const texto=document.getElementById('deptxt').value;
  const estrelas=+document.getElementById('depest').value;
  await fetch('/admin/depo/criar',{{method:'POST',headers:H(),body:JSON.stringify({{nome,texto,estrelas}})}});
  document.getElementById('depnome').value='';document.getElementById('deptxt').value='';
  listarDepo();
}}
async function listarDepo(){{
  const r=await fetch('/admin/depo/listar',{{headers:H()}});
  const d=await r.json();
  let h='<table><tr><th>Cliente</th><th>Depoimento</th><th>★</th><th></th></tr>';
  for(const x of (d.depoimentos||[])){{
    h+='<tr><td>'+x.nome+'</td><td class=muted>'+x.texto+'</td><td>'+x.estrelas+'</td>'+
       '<td><button class=danger onclick="delDepo('+x.id+')">Excluir</button></td></tr>';
  }}
  document.getElementById('listadep').innerHTML=h+'</table>';
}}
async function delDepo(id){{await fetch('/admin/depo/remover',{{method:'POST',headers:H(),body:JSON.stringify({{id}})}});listarDepo();}}
</script>"""


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=porta)
