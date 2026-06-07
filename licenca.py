"""
licenca.py
-----------
Gera e valida keys, agora com NÍVEL de plano embutido (Básico..Ultimate).

Formato da key:  AAAA-BBBB-Nx??-AAAAMMDD-SSSSSSSS
- AAAA/BBBB : aleatório
- Nx??      : "N" + nível (1..4) + 2 aleatórios   (ex.: N3F1 = nível 3)
- AAAAMMDD  : data de expiração
- SSSSSSSS  : assinatura (impede falsificação)

TROQUE a CHAVE_SECRETA por uma sua, longa e aleatória. Ela precisa ser a MESMA
no app (otimizador.py) e no servidor (servidor.py).
"""

import hmac
import hashlib
import secrets
from datetime import datetime, timedelta

# >>> TROQUE ISTO <<<
CHAVE_SECRETA = b"TROQUE_ESTA_CHAVE_SECRETA_POR_UMA_SUA_LONGA_E_ALEATORIA_2025"

# Níveis de plano (número -> nome)
NIVEIS = {1: "Básico", 2: "Turbo", 3: "Pro", 4: "Ultimate"}


def _assinar(payload: str) -> str:
    return hmac.new(CHAVE_SECRETA, payload.encode(), hashlib.sha256).hexdigest()[:8].upper()


def gerar_key(dias_validade: int = 30, nivel: int = 4) -> str:
    """Gera uma key válida por X dias e com o nível de plano indicado (1..4)."""
    try:
        nivel = int(nivel)
    except Exception:
        nivel = 4
    if nivel not in NIVEIS:
        nivel = 4
    p1 = secrets.token_hex(2).upper()
    p2 = secrets.token_hex(2).upper()
    bloco_nivel = "N" + str(nivel) + secrets.token_hex(1).upper()  # 4 caracteres
    expira = (datetime.now() + timedelta(days=dias_validade)).strftime("%Y%m%d")
    payload = p1 + p2 + bloco_nivel + expira
    sig = _assinar(payload)
    return f"{p1}-{p2}-{bloco_nivel}-{expira}-{sig}"


def nivel_da_key(key: str) -> int:
    """Lê o nível de plano da key (1..4). Keys sem marcação contam como Ultimate."""
    try:
        b = key.strip().upper().split("-")[2]
        if b.startswith("N") and b[1].isdigit():
            n = int(b[1])
            return n if n in NIVEIS else 4
    except Exception:
        pass
    return 4


def validar_key(key: str):
    """Valida assinatura e validade. Retorna (bool, mensagem)."""
    try:
        partes = key.strip().upper().split("-")
        if len(partes) != 5:
            return False, "Formato de key inválido."
        p1, p2, b3, expira, sig = partes
        payload = p1 + p2 + b3 + expira
        if not hmac.compare_digest(_assinar(payload), sig):
            return False, "Key inválida (assinatura não confere)."
        validade = datetime.strptime(expira, "%Y%m%d")
        if datetime.now().date() > validade.date():
            return False, f"Key expirada em {validade.strftime('%d/%m/%Y')}."
        return True, f"Key válida até {validade.strftime('%d/%m/%Y')}."
    except Exception:
        return False, "Erro ao ler a key."
