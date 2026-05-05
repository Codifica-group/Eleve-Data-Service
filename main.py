import os
import sqlite3
import unicodedata
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

logger = logging.getLogger("eleve.data_service")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    logger.addHandler(handler)
logger.setLevel(LOG_LEVEL)
logger.propagate = False

CHAVE_API_DOG = os.getenv(
    "CHAVE_API_DOG",
    os.getenv("DOG_API_KEY", ""),
).strip()
CAMINHO_BANCO_DADOS = str(
    Path(
        os.getenv(
            "CAMINHO_BANCO_DADOS_PY",
            os.getenv("DADOS_PY_DB_PATH", str(BASE_DIR / "dados_py.db")),
        )
    )
    .expanduser()
    .resolve()
)
URL_BASE_API_DOG = "https://api.thedogapi.com/v1"
HORA_SINCRONIZACAO_UTC = int(os.getenv("HORA_SINCRONIZACAO_UTC", "3"))
TOTAL_RACAS_MINIMO_SINCRONIZADAS = int(
    os.getenv("TOTAL_RACAS_MINIMO_SINCRONIZADAS", "200")
)

RACA_DE_PARA = {
    "foxhound americano": "American Foxhound",
    "lulu da pomerania": "Pomeranian",
    "spitz alemao anao": "Pomeranian",
    "spitz alemao": "Pomeranian",
    "salsicha": "Dachshund",
    "buldogue frances": "French Bulldog",
    "bulldog frances": "French Bulldog",
    "buldogue ingles": "English Bulldog",
    "pastor alemao": "German Shepherd Dog",
    "pastor australiano": "Australian Shepherd",
    "cocker spaniel ingles": "English Cocker Spaniel",
    "cavalier king charles spaniel": "Cavalier King Charles Spaniel",
    "havanes": "Havanese",
    "maltes": "Maltese",
    "shih tzu": "Shih Tzu",
    "shihtzu": "Shih Tzu",
    "pinscher": "Miniature Pinscher",
    "yorkshire": "Yorkshire Terrier",
    "labrador": "Labrador Retriever",
    "golden": "Golden Retriever",
    "beagle": "Beagle",
    "border collie": "Border Collie",
    "galgo afegao": "Afghan Hound",
    "pug": "Pug",
    "rottweiler": "Rottweiler",
    "husky siberiano": "Siberian Husky",
    "doberman": "Doberman Pinscher",
}
PALAVRAS_IGNORADAS_RACA = {"de", "da", "do", "das", "dos", "e"}
TOKENS_TRADUZIDOS_RACA = {
    "americano": "American",
    "americana": "American",
    "ingles": "English",
    "inglesa": "English",
    "frances": "French",
    "francesa": "French",
    "alemao": "German",
    "alema": "German",
    "australiano": "Australian",
    "australiana": "Australian",
    "siberiano": "Siberian",
    "siberiana": "Siberian",
    "japones": "Japanese",
    "japonesa": "Japanese",
    "chines": "Chinese",
    "chinesa": "Chinese",
    "tibetano": "Tibetan",
    "tibetana": "Tibetan",
    "russo": "Russian",
    "russa": "Russian",
    "escoces": "Scottish",
    "escocesa": "Scottish",
    "gales": "Welsh",
    "italiano": "Italian",
    "italiana": "Italian",
    "belga": "Belgian",
    "irlandes": "Irish",
    "irlandesa": "Irish",
    "espanhol": "Spanish",
    "espanhola": "Spanish",
    "finlandes": "Finnish",
    "finlandesa": "Finnish",
    "sueco": "Swedish",
    "sueca": "Swedish",
    "noruegues": "Norwegian",
    "norueguesa": "Norwegian",
    "holandes": "Dutch",
    "holandesa": "Dutch",
    "foxhound": "Foxhound",
    "bulldogue": "Bulldog",
    "bulldog": "Bulldog",
    "retriever": "Retriever",
    "spaniel": "Spaniel",
    "terrier": "Terrier",
    "pastor": "Shepherd",
    "shepherd": "Shepherd",
    "collie": "Collie",
    "corgi": "Corgi",
    "mastiff": "Mastiff",
    "pinscher": "Pinscher",
    "husky": "Husky",
    "schnauzer": "Schnauzer",
    "poodle": "Poodle",
    "spitz": "Spitz",
    "setter": "Setter",
    "hound": "Hound",
    "boxer": "Boxer",
    "beagle": "Beagle",
    "pug": "Pug",
    "rottweiler": "Rottweiler",
    "pomeranian": "Pomeranian",
}
ADJETIVOS_NACIONALIDADE_RACA = {
    "American",
    "English",
    "French",
    "German",
    "Australian",
    "Siberian",
    "Japanese",
    "Chinese",
    "Tibetan",
    "Russian",
    "Scottish",
    "Welsh",
    "Italian",
    "Belgian",
    "Irish",
    "Spanish",
    "Finnish",
    "Swedish",
    "Norwegian",
    "Dutch",
}

app = FastAPI(title="Dados Externos - Eleve", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

scheduler = BackgroundScheduler(timezone="UTC")


class RacaCadastroRequest(BaseModel):
    nome: str
    grupo: Optional[str] = None
    temperamento: Optional[str] = None
    expectativa_vida: Optional[str] = None
    peso: Optional[str] = None
    altura: Optional[str] = None


@app.middleware("http")
async def registrar_requisicoes(request: Request, call_next):
    inicio = time.perf_counter()
    cliente = request.client.host if request.client else "desconhecido"
    query = str(request.url.query or "")
    logger.debug(
        "HTTP -> %s %s | q=%s | ip=%s",
        request.method,
        request.url.path,
        query or "-",
        cliente,
    )

    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001
        duracao_ms = round((time.perf_counter() - inicio) * 1000)
        logger.exception(
            "HTTP !! %s %s | q=%s | ip=%s | %sms | erro=%s",
            request.method,
            request.url.path,
            query or "-",
            cliente,
            duracao_ms,
            exc,
        )
        raise

    duracao_ms = round((time.perf_counter() - inicio) * 1000)
    logger.info(
        "HTTP %s %s | %s | %sms",
        request.method,
        request.url.path,
        response.status_code,
        duracao_ms,
    )
    return response


def agora_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def obter_conexao_banco():
    try:
        conexao = sqlite3.connect(CAMINHO_BANCO_DADOS)
    except sqlite3.Error:
        logger.exception("DB falha | path=%s", CAMINHO_BANCO_DADOS)
        raise
    conexao.row_factory = sqlite3.Row
    try:
        yield conexao
        conexao.commit()
    finally:
        conexao.close()


def obter_colunas_tabela(conexao: sqlite3.Connection, tabela: str) -> set[str]:
    return {
        linha["name"]
        for linha in conexao.execute(f"PRAGMA table_info({tabela})").fetchall()
    }


def tabela_existe(conexao: sqlite3.Connection, tabela: str) -> bool:
    linha = conexao.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (tabela,),
    ).fetchone()
    return linha is not None


def contar_registros_tabela(conexao: sqlite3.Connection, tabela: str) -> int:
    if not tabela_existe(conexao, tabela):
        return 0
    return conexao.execute(f"SELECT COUNT(*) AS total FROM {tabela}").fetchone()["total"]


def logar_configuracao_servico() -> None:
    caminho_banco = Path(CAMINHO_BANCO_DADOS)
    banco_existe = caminho_banco.exists()
    tamanho_bytes = caminho_banco.stat().st_size if banco_existe else 0
    logger.info(
        "Startup | db=%s | key=%s | sync=%02d:00Z | min=%s | levels=DEBUG:diag INFO:fluxo WARN:alerta ERROR:fatal",
        caminho_banco.name,
        "on" if CHAVE_API_DOG else "off",
        HORA_SINCRONIZACAO_UTC,
        TOTAL_RACAS_MINIMO_SINCRONIZADAS,
    )
    logger.debug(
        "Startup detalhe | path=%s | exists=%s | size=%s | cwd=%s",
        caminho_banco,
        banco_existe,
        tamanho_bytes,
        Path.cwd(),
    )
    logger.debug(
        "Startup detalhe | log_level=%s",
        logging.getLevelName(LOG_LEVEL),
    )


def logar_diagnostico_banco(contexto: str) -> None:
    tabelas_esperadas = {
        "dogapi_cache",
        "eventos_consulta_raca",
        "execucoes_sincronizacao",
        "racas_externas",
        "racas_locais",
    }
    with obter_conexao_banco() as conexao:
        versao_sqlite = conexao.execute(
            "SELECT sqlite_version() AS versao"
        ).fetchone()["versao"]
        tabelas = [
            linha["name"]
            for linha in conexao.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                ORDER BY name ASC
                """
            ).fetchall()
        ]
        tabelas_ausentes = sorted(tabelas_esperadas - set(tabelas))
        total_racas_locais = contar_registros_tabela(conexao, "racas_locais")
        total_racas_externas = contar_registros_tabela(conexao, "racas_externas")
        total_cache_dogapi = contar_registros_tabela(conexao, "dogapi_cache")
        total_eventos_consulta = contar_registros_tabela(conexao, "eventos_consulta_raca")
        total_execucoes_sync = contar_registros_tabela(conexao, "execucoes_sincronizacao")

    caminho_banco = Path(CAMINHO_BANCO_DADOS)
    banco_existe = caminho_banco.exists()
    tamanho_bytes = caminho_banco.stat().st_size if banco_existe else 0
    nivel_resumo = logging.INFO if contexto == "apos_sincronizacao_inicial" else logging.DEBUG
    logger.log(
        nivel_resumo,
        "DB ok | ctx=%s | db=%s | local=%s | externa=%s | cache=%s | consultas=%s | sync=%s",
        contexto,
        caminho_banco.name,
        total_racas_locais,
        total_racas_externas,
        total_cache_dogapi,
        total_eventos_consulta,
        total_execucoes_sync,
    )
    logger.debug(
        "DB detalhe | ctx=%s | path=%s | exists=%s | size=%s | sqlite=%s | tables=%s | missing=%s",
        contexto,
        caminho_banco,
        banco_existe,
        tamanho_bytes,
        versao_sqlite,
        ", ".join(tabelas) or "-",
        ", ".join(tabelas_ausentes) or "-",
    )


def garantir_colunas_racas_externas(conexao: sqlite3.Connection) -> None:
    colunas_existentes = obter_colunas_tabela(conexao, "racas_externas")
    colunas_esperadas = {
        "nome_original": "TEXT",
        "nome_alternativo": "TEXT",
        "peso_imperial": "TEXT",
        "altura_imperial": "TEXT",
        "origem_raca": "TEXT",
        "criado_para": "TEXT",
        "referencia_imagem_id": "TEXT",
        "imagem_url": "TEXT",
        "descricao": "TEXT",
    }

    for coluna, definicao in colunas_esperadas.items():
        if coluna not in colunas_existentes:
            conexao.execute(
                f"ALTER TABLE racas_externas ADD COLUMN {coluna} {definicao}"
            )


def inicializar_banco() -> None:
    with obter_conexao_banco() as conexao:
        conexao.execute(
            """
            CREATE TABLE IF NOT EXISTS dogapi_cache (
                id_dog_api INTEGER PRIMARY KEY,
                nome TEXT NOT NULL,
                nome_alternativo TEXT,
                grupo TEXT,
                temperamento TEXT,
                expectativa_vida TEXT,
                peso_metrico TEXT,
                peso_imperial TEXT,
                altura_metrica TEXT,
                altura_imperial TEXT,
                origem_raca TEXT,
                criado_para TEXT,
                referencia_imagem_id TEXT,
                atualizado_em TEXT NOT NULL
            )
            """
        )

        conexao.execute(
            """
            CREATE TABLE IF NOT EXISTS racas_externas (
                id_raca INTEGER PRIMARY KEY,
                nome TEXT NOT NULL,
                nome_original TEXT,
                nome_alternativo TEXT,
                grupo TEXT,
                temperamento TEXT,
                expectativa_vida TEXT,
                peso_metrico TEXT,
                peso_imperial TEXT,
                altura_metrica TEXT,
                altura_imperial TEXT,
                origem TEXT DEFAULT 'TheDogAPI',
                origem_raca TEXT,
                criado_para TEXT,
                referencia_imagem_id TEXT,
                imagem_url TEXT,
                descricao TEXT,
                atualizado_em TEXT NOT NULL
            )
            """
        )
        garantir_colunas_racas_externas(conexao)

        conexao.execute(
            """
            CREATE TABLE IF NOT EXISTS racas_locais (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL UNIQUE,
                grupo TEXT,
                temperamento TEXT,
                expectativa_vida TEXT,
                peso TEXT,
                altura TEXT,
                origem TEXT DEFAULT 'usuario',
                cadastrado_em TEXT NOT NULL
            )
            """
        )

        conexao.execute(
            """
            CREATE TABLE IF NOT EXISTS execucoes_sincronizacao (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                iniciado_em TEXT NOT NULL,
                finalizado_em TEXT,
                status TEXT NOT NULL,
                total_registros INTEGER DEFAULT 0,
                erro TEXT
            )
            """
        )

        conexao.execute(
            """
            CREATE TABLE IF NOT EXISTS eventos_consulta_raca (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome_consultado TEXT NOT NULL,
                id_raca INTEGER,
                encontrado INTEGER NOT NULL,
                origem TEXT,
                consultado_em TEXT NOT NULL
            )
            """
        )


def mesclar_banco_legado_se_necessario() -> None:
    caminho_canonico = Path(CAMINHO_BANCO_DADOS).resolve()
    caminho_legado = (Path.cwd() / "dados_py.db").resolve()

    if caminho_legado == caminho_canonico or not caminho_legado.exists():
        return

    logger.warning(
        "Legado detectado | src=%s | dst=%s",
        caminho_legado.name,
        caminho_canonico.name,
    )

    total_racas_locais = 0
    total_racas_externas = 0
    total_cache = 0

    try:
        with sqlite3.connect(caminho_legado) as origem:
            origem.row_factory = sqlite3.Row
            with obter_conexao_banco() as destino:
                try:
                    for linha in origem.execute("SELECT * FROM racas_locais"):
                        salvar_raca_local(
                            destino,
                            linha["nome"],
                            linha["grupo"],
                            linha["temperamento"],
                            linha["expectativa_vida"],
                            linha["peso"],
                            linha["altura"],
                            linha["origem"],
                        )
                        total_racas_locais += 1
                except sqlite3.OperationalError:
                    pass

                try:
                    for linha in origem.execute("SELECT * FROM racas_externas"):
                        salvar_raca_externa(
                            destino,
                            montar_dados_raca_externa_da_linha(linha),
                        )
                        total_racas_externas += 1
                except sqlite3.OperationalError:
                    pass

                try:
                    for linha in origem.execute("SELECT * FROM dogapi_cache"):
                        destino.execute(
                            """
                            INSERT INTO dogapi_cache (
                                id_dog_api, nome, nome_alternativo, grupo, temperamento,
                                expectativa_vida, peso_metrico, peso_imperial,
                                altura_metrica, altura_imperial,
                                origem_raca, criado_para, referencia_imagem_id, atualizado_em
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(id_dog_api) DO UPDATE SET
                                nome=excluded.nome,
                                nome_alternativo=excluded.nome_alternativo,
                                grupo=excluded.grupo,
                                temperamento=excluded.temperamento,
                                expectativa_vida=excluded.expectativa_vida,
                                peso_metrico=excluded.peso_metrico,
                                peso_imperial=excluded.peso_imperial,
                                altura_metrica=excluded.altura_metrica,
                                altura_imperial=excluded.altura_imperial,
                                origem_raca=excluded.origem_raca,
                                criado_para=excluded.criado_para,
                                referencia_imagem_id=excluded.referencia_imagem_id,
                                atualizado_em=excluded.atualizado_em
                            """,
                            (
                                linha["id_dog_api"],
                                linha["nome"],
                                linha["nome_alternativo"],
                                linha["grupo"],
                                linha["temperamento"],
                                linha["expectativa_vida"],
                                linha["peso_metrico"],
                                linha["peso_imperial"],
                                linha["altura_metrica"],
                                linha["altura_imperial"],
                                linha["origem_raca"],
                                linha["criado_para"],
                                linha["referencia_imagem_id"],
                                linha["atualizado_em"],
                            ),
                        )
                        salvar_raca_externa(
                            destino,
                            montar_dados_raca_externa_da_linha(linha),
                        )
                        total_cache += 1
                except sqlite3.OperationalError:
                    pass
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Legado falhou | src=%s | erro=%s",
            caminho_legado.name,
            exc,
        )
        return

    logger.info(
        "Legado mesclado | local=%s | externa=%s | cache=%s",
        total_racas_locais,
        total_racas_externas,
        total_cache,
    )


def montar_cabecalhos_api_dog() -> dict:
    cabecalhos = {"Accept": "application/json"}
    if CHAVE_API_DOG:
        cabecalhos["x-api-key"] = CHAVE_API_DOG
    return cabecalhos


def normalizar_texto(
    valor: Optional[str],
    padrao: str = "Nao informado",
) -> str:
    texto = str(valor or "").strip()
    return texto if texto else padrao


def normalizar_chave_raca(valor: Optional[str]) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or "").strip().lower())
    texto_sem_acento = "".join(char for char in texto if not unicodedata.combining(char))
    return " ".join(texto_sem_acento.split())


def traduzir_raca_para_consulta_externa(nome: str) -> str:
    candidatos = gerar_candidatos_consulta_externa(nome)
    return candidatos[0] if candidatos else ""


def capitalizar_palavra(nome: str) -> str:
    texto = str(nome or "").strip()
    if not texto:
        return ""
    return texto[:1].upper() + texto[1:].lower()


def montar_candidato_heuristico(nome: str) -> str:
    nome_limpo = str(nome or "").strip()
    if not nome_limpo:
        return ""

    tokens = [
        token
        for token in normalizar_chave_raca(nome_limpo).split(" ")
        if token and token not in PALAVRAS_IGNORADAS_RACA
    ]

    if not tokens:
        return ""

    tokens_traduzidos = [
        TOKENS_TRADUZIDOS_RACA.get(token, capitalizar_palavra(token))
        for token in tokens
    ]

    ultimo = tokens_traduzidos[-1]
    if ultimo in ADJETIVOS_NACIONALIDADE_RACA and len(tokens_traduzidos) > 1:
        return " ".join([ultimo, *tokens_traduzidos[:-1]])

    return " ".join(tokens_traduzidos)


def gerar_candidatos_consulta_externa(nome: str) -> list[str]:
    nome_limpo = str(nome or "").strip()
    if not nome_limpo:
        return []

    candidatos: list[str] = []
    vistos: set[str] = set()

    def adicionar(valor: Optional[str]) -> None:
        texto = str(valor or "").strip()
        if not texto:
            return
        chave = texto.lower()
        if chave in vistos:
            return
        vistos.add(chave)
        candidatos.append(texto)

    adicionar(RACA_DE_PARA.get(normalizar_chave_raca(nome_limpo)))
    adicionar(montar_candidato_heuristico(nome_limpo))
    adicionar(nome_limpo)
    return candidatos


def obter_chave_canonica_raca(nome: str) -> str:
    return normalizar_chave_raca(traduzir_raca_para_consulta_externa(nome))


def linha_possui_dados_uteis(linha: Optional[sqlite3.Row]) -> bool:
    if linha is None:
        return False

    campos = (
        "grupo",
        "temperamento",
        "expectativa_vida",
        "peso",
        "altura",
        "peso_metrico",
        "altura_metrica",
    )

    for campo in campos:
        if campo not in linha.keys():
            continue
        valor = normalizar_texto(linha[campo])
        if valor != "Nao informado":
            return True

    return False


def obter_valor_coluna(linha, coluna: str, padrao=None):
    if linha is None:
        return padrao

    try:
        if coluna in linha.keys():
            valor = linha[coluna]
            return padrao if valor is None else valor
    except AttributeError:
        pass

    if isinstance(linha, dict) and coluna in linha:
        valor = linha[coluna]
        return padrao if valor is None else valor

    return padrao


def montar_dados_raca_externa_do_item(item: dict) -> dict:
    return {
        "id_raca": item.get("id"),
        "nome": normalizar_texto(item.get("name"), padrao="Raca sem nome"),
        "nome_original": normalizar_texto(item.get("name"), padrao="Raca sem nome"),
        "nome_alternativo": normalizar_texto(item.get("alt_names")),
        "grupo": normalizar_texto(item.get("breed_group")),
        "temperamento": normalizar_texto(item.get("temperament")),
        "expectativa_vida": normalizar_texto(item.get("life_span")),
        "peso_metrico": normalizar_texto((item.get("weight") or {}).get("metric")),
        "peso_imperial": normalizar_texto((item.get("weight") or {}).get("imperial")),
        "altura_metrica": normalizar_texto((item.get("height") or {}).get("metric")),
        "altura_imperial": normalizar_texto((item.get("height") or {}).get("imperial")),
        "origem": "TheDogAPI",
        "origem_raca": normalizar_texto(item.get("origin")),
        "criado_para": normalizar_texto(item.get("bred_for")),
        "referencia_imagem_id": normalizar_texto(item.get("reference_image_id")),
        "imagem_url": normalizar_texto((item.get("image") or {}).get("url")),
        "descricao": normalizar_texto(item.get("description")),
        "atualizado_em": agora_utc_iso(),
    }


def montar_dados_raca_externa_da_linha(linha) -> dict:
    return {
        "id_raca": obter_valor_coluna(linha, "id_raca", obter_valor_coluna(linha, "id_dog_api")),
        "nome": normalizar_texto(obter_valor_coluna(linha, "nome"), padrao="Raca sem nome"),
        "nome_original": normalizar_texto(
            obter_valor_coluna(linha, "nome_original", obter_valor_coluna(linha, "nome")),
            padrao="Raca sem nome",
        ),
        "nome_alternativo": normalizar_texto(obter_valor_coluna(linha, "nome_alternativo")),
        "grupo": normalizar_texto(obter_valor_coluna(linha, "grupo")),
        "temperamento": normalizar_texto(obter_valor_coluna(linha, "temperamento")),
        "expectativa_vida": normalizar_texto(obter_valor_coluna(linha, "expectativa_vida")),
        "peso_metrico": normalizar_texto(obter_valor_coluna(linha, "peso_metrico")),
        "peso_imperial": normalizar_texto(obter_valor_coluna(linha, "peso_imperial")),
        "altura_metrica": normalizar_texto(obter_valor_coluna(linha, "altura_metrica")),
        "altura_imperial": normalizar_texto(obter_valor_coluna(linha, "altura_imperial")),
        "origem": normalizar_texto(obter_valor_coluna(linha, "origem"), padrao="TheDogAPI"),
        "origem_raca": normalizar_texto(
            obter_valor_coluna(linha, "origem_raca", obter_valor_coluna(linha, "origem")),
        ),
        "criado_para": normalizar_texto(obter_valor_coluna(linha, "criado_para")),
        "referencia_imagem_id": normalizar_texto(obter_valor_coluna(linha, "referencia_imagem_id")),
        "imagem_url": normalizar_texto(obter_valor_coluna(linha, "imagem_url")),
        "descricao": normalizar_texto(obter_valor_coluna(linha, "descricao")),
        "atualizado_em": normalizar_texto(obter_valor_coluna(linha, "atualizado_em"), padrao=agora_utc_iso()),
    }


def salvar_raca_externa(conexao: sqlite3.Connection, dados: dict) -> None:
    id_raca = dados.get("id_raca")
    if id_raca is None:
        return

    conexao.execute(
        """
        INSERT INTO racas_externas (
            id_raca, nome, nome_original, nome_alternativo, grupo, temperamento,
            expectativa_vida, peso_metrico, peso_imperial, altura_metrica,
            altura_imperial, origem, origem_raca, criado_para,
            referencia_imagem_id, imagem_url, descricao, atualizado_em
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id_raca) DO UPDATE SET
            nome=excluded.nome,
            nome_original=excluded.nome_original,
            nome_alternativo=excluded.nome_alternativo,
            grupo=excluded.grupo,
            temperamento=excluded.temperamento,
            expectativa_vida=excluded.expectativa_vida,
            peso_metrico=excluded.peso_metrico,
            peso_imperial=excluded.peso_imperial,
            altura_metrica=excluded.altura_metrica,
            altura_imperial=excluded.altura_imperial,
            origem=excluded.origem,
            origem_raca=excluded.origem_raca,
            criado_para=excluded.criado_para,
            referencia_imagem_id=excluded.referencia_imagem_id,
            imagem_url=excluded.imagem_url,
            descricao=excluded.descricao,
            atualizado_em=excluded.atualizado_em
        """,
        (
            id_raca,
            dados["nome"],
            dados["nome_original"],
            dados["nome_alternativo"],
            dados["grupo"],
            dados["temperamento"],
            dados["expectativa_vida"],
            dados["peso_metrico"],
            dados["peso_imperial"],
            dados["altura_metrica"],
            dados["altura_imperial"],
            dados["origem"],
            dados["origem_raca"],
            dados["criado_para"],
            dados["referencia_imagem_id"],
            dados["imagem_url"],
            dados["descricao"],
            dados["atualizado_em"],
        ),
    )


def montar_resposta_info_raca(nome_consultado: str, linha, fonte_padrao: str) -> dict:
    race_id = obter_valor_coluna(linha, "id_raca", obter_valor_coluna(linha, "id_dog_api"))
    nome_base = normalizar_texto(
        obter_valor_coluna(linha, "nome"),
        padrao=nome_consultado,
    )
    nome_externo = normalizar_texto(
        obter_valor_coluna(linha, "nome_original", nome_base),
        padrao=nome_base,
    )

    return {
        "nome": nome_consultado,
        "nomeExterno": nome_externo,
        "grupo": normalizar_texto(obter_valor_coluna(linha, "grupo")),
        "temperamento": normalizar_texto(obter_valor_coluna(linha, "temperamento")),
        "expectativaVida": normalizar_texto(obter_valor_coluna(linha, "expectativa_vida")),
        "peso": normalizar_texto(
            obter_valor_coluna(linha, "peso", obter_valor_coluna(linha, "peso_metrico"))
        ),
        "pesoImperial": normalizar_texto(obter_valor_coluna(linha, "peso_imperial")),
        "altura": normalizar_texto(
            obter_valor_coluna(linha, "altura", obter_valor_coluna(linha, "altura_metrica"))
        ),
        "alturaImperial": normalizar_texto(obter_valor_coluna(linha, "altura_imperial")),
        "origemRaca": normalizar_texto(
            obter_valor_coluna(linha, "origem_raca", obter_valor_coluna(linha, "origem"))
        ),
        "proposito": normalizar_texto(obter_valor_coluna(linha, "criado_para")),
        "referenciaImagemId": normalizar_texto(obter_valor_coluna(linha, "referencia_imagem_id")),
        "imagemUrl": normalizar_texto(obter_valor_coluna(linha, "imagem_url")),
        "descricao": normalizar_texto(obter_valor_coluna(linha, "descricao")),
        "fonte": normalizar_texto(obter_valor_coluna(linha, "origem"), padrao=fonte_padrao),
        "raceId": race_id,
    }


def buscar_melhor_correspondencia(nome: str, linhas: list[sqlite3.Row]) -> Optional[sqlite3.Row]:
    chave_normalizada = normalizar_chave_raca(nome)
    chave_canonica = obter_chave_canonica_raca(nome)

    correspondencias_parciais = []

    for linha in linhas:
        nome_linha = linha["nome"]
        chave_linha = normalizar_chave_raca(nome_linha)
        chave_canonica_linha = obter_chave_canonica_raca(nome_linha)

        if chave_linha == chave_normalizada or chave_canonica_linha == chave_canonica:
            return linha

        if (
            chave_normalizada in chave_linha
            or chave_linha in chave_normalizada
            or chave_canonica in chave_canonica_linha
            or chave_canonica_linha in chave_canonica
        ):
            correspondencias_parciais.append(linha)

    return correspondencias_parciais[0] if correspondencias_parciais else None


def salvar_no_cache_dogapi(conexao: sqlite3.Connection, item: dict) -> None:
    id_dog_api = item.get("id")
    if id_dog_api is None:
        return

    conexao.execute(
        """
        INSERT INTO dogapi_cache (
            id_dog_api, nome, nome_alternativo, grupo, temperamento,
            expectativa_vida, peso_metrico, peso_imperial,
            altura_metrica, altura_imperial,
            origem_raca, criado_para, referencia_imagem_id, atualizado_em
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id_dog_api) DO UPDATE SET
            nome=excluded.nome,
            nome_alternativo=excluded.nome_alternativo,
            grupo=excluded.grupo,
            temperamento=excluded.temperamento,
            expectativa_vida=excluded.expectativa_vida,
            peso_metrico=excluded.peso_metrico,
            peso_imperial=excluded.peso_imperial,
            altura_metrica=excluded.altura_metrica,
            altura_imperial=excluded.altura_imperial,
            origem_raca=excluded.origem_raca,
            criado_para=excluded.criado_para,
            referencia_imagem_id=excluded.referencia_imagem_id,
            atualizado_em=excluded.atualizado_em
        """,
        (
            id_dog_api,
            normalizar_texto(item.get("name"), padrao="Raca sem nome"),
            normalizar_texto(item.get("alt_names")),
            normalizar_texto(item.get("breed_group")),
            normalizar_texto(item.get("temperament")),
            normalizar_texto(item.get("life_span")),
            normalizar_texto((item.get("weight") or {}).get("metric")),
            normalizar_texto((item.get("weight") or {}).get("imperial")),
            normalizar_texto((item.get("height") or {}).get("metric")),
            normalizar_texto((item.get("height") or {}).get("imperial")),
            normalizar_texto(item.get("origin")),
            normalizar_texto(item.get("bred_for")),
            normalizar_texto(item.get("reference_image_id")),
            agora_utc_iso(),
        ),
    )


def inserir_ou_atualizar_raca(conexao: sqlite3.Connection, item: dict) -> None:
    id_raca = item.get("id")
    if id_raca is None:
        return

    salvar_no_cache_dogapi(conexao, item)
    salvar_raca_externa(conexao, montar_dados_raca_externa_do_item(item))


def salvar_raca_local(
    conexao: sqlite3.Connection,
    nome: str,
    grupo: str,
    temperamento: str,
    expectativa_vida: str,
    peso: str,
    altura: str,
    origem: str = "usuario",
) -> None:
    conexao.execute(
        """
        INSERT INTO racas_locais (nome, grupo, temperamento, expectativa_vida, peso, altura, origem, cadastrado_em)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(nome) DO UPDATE SET
            grupo=excluded.grupo,
            temperamento=excluded.temperamento,
            expectativa_vida=excluded.expectativa_vida,
            peso=excluded.peso,
            altura=excluded.altura,
            origem=excluded.origem,
            cadastrado_em=excluded.cadastrado_em
        """,
        (nome, grupo, temperamento, expectativa_vida, peso, altura, origem, agora_utc_iso()),
    )


def buscar_raca_local_por_nome(nome: str) -> Optional[sqlite3.Row]:
    with obter_conexao_banco() as conexao:
        linhas = conexao.execute(
            """
            SELECT * FROM racas_locais
            ORDER BY nome ASC
            """
        ).fetchall()
    resultado = buscar_melhor_correspondencia(nome, linhas)
    logger.debug(
        "DB local | q=%s | n=%s | hit=%s",
        nome,
        len(linhas),
        obter_valor_coluna(resultado, "nome", "-"),
    )
    return resultado


def buscar_raca_por_nome(nome: str) -> Optional[sqlite3.Row]:
    with obter_conexao_banco() as conexao:
        linhas = conexao.execute(
            """
            SELECT *
            FROM racas_externas
            ORDER BY nome ASC
            """
        ).fetchall()
    resultado = buscar_melhor_correspondencia(nome, linhas)
    logger.debug(
        "DB externa | q=%s | n=%s | hit=%s",
        nome,
        len(linhas),
        obter_valor_coluna(resultado, "nome", "-"),
    )
    return resultado


def buscar_raca_no_cache_por_nome(nome: str) -> Optional[sqlite3.Row]:
    with obter_conexao_banco() as conexao:
        linhas = conexao.execute(
            """
            SELECT
                id_dog_api AS id_raca,
                nome,
                nome AS nome_original,
                nome_alternativo,
                grupo,
                temperamento,
                expectativa_vida,
                peso_metrico,
                peso_imperial,
                altura_metrica,
                altura_imperial,
                'TheDogAPI' AS origem
                , origem_raca
                , criado_para
                , referencia_imagem_id
                , 'Nao informado' AS imagem_url
                , 'Nao informado' AS descricao
            FROM dogapi_cache
            ORDER BY nome ASC
            """
        ).fetchall()
    resultado = buscar_melhor_correspondencia(nome, linhas)
    logger.debug(
        "DB cache | q=%s | n=%s | hit=%s",
        nome,
        len(linhas),
        obter_valor_coluna(resultado, "nome", "-"),
    )
    return resultado


def buscar_no_dogapi_por_nome(nome: str) -> Optional[dict]:
    """Busca on-demand no TheDogAPI. Persiste no cache local e retorna None se não encontrado."""
    logger.info(
        "DogAPI GET /breeds/search | q=%s",
        nome,
    )
    try:
        resposta = requests.get(
            f"{URL_BASE_API_DOG}/breeds/search",
            params={"q": nome},
            headers=montar_cabecalhos_api_dog(),
            timeout=10,
        )
        resposta.raise_for_status()
        resultados = resposta.json()
        logger.info(
            "DogAPI %s /breeds/search | q=%s | hits=%s",
            resposta.status_code,
            nome,
            len(resultados),
        )
        if not resultados:
            logger.info("DogAPI miss | q=%s", nome)
            return None
        item = resultados[0]
        with obter_conexao_banco() as conexao:
            inserir_ou_atualizar_raca(conexao, item)
        logger.info(
            "DogAPI hit | q=%s | nome=%s | id=%s | save=cache,externa",
            nome,
            item.get("name"),
            item.get("id"),
        )
        return item
    except Exception as exc:  # noqa: BLE001
        logger.warning("DogAPI falhou | q=%s | erro=%s", nome, exc)
        return None


def contar_racas_externas() -> int:
    with obter_conexao_banco() as conexao:
        return conexao.execute(
            "SELECT COUNT(*) AS total FROM racas_externas"
        ).fetchone()["total"]


def restaurar_racas_externas_a_partir_do_cache() -> int:
    with obter_conexao_banco() as conexao:
        linhas = conexao.execute(
            """
            SELECT *
            FROM dogapi_cache
            ORDER BY nome ASC
            """
        ).fetchall()

        total = 0
        for linha in linhas:
            salvar_raca_externa(
                conexao,
                montar_dados_raca_externa_da_linha(linha),
            )
            total += 1

    return total


def sincronizar_racas_iniciais_se_necessario() -> None:
    total_racas = contar_racas_externas()
    if total_racas >= TOTAL_RACAS_MINIMO_SINCRONIZADAS:
        logger.info("Startup | base_externa=ok | total=%s", total_racas)
        return

    if total_racas > 0:
        logger.info(
            "Startup | base_externa=incompleta | total=%s | min=%s",
            total_racas,
            TOTAL_RACAS_MINIMO_SINCRONIZADAS,
        )

    total_restaurado = restaurar_racas_externas_a_partir_do_cache()
    if total_restaurado > 0:
        logger.info(
            "Startup | base_externa=restaurada_cache | total=%s",
            total_restaurado,
        )

    logger.info("Startup | base_externa=sync_full")
    try:
        resultado = executar_sincronizacao_etl()
        logger.info(
            "Startup | sync=%s | total=%s",
            resultado.get("status"),
            resultado.get("totalRegistros"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Startup | sync=falhou | erro=%s", exc)


def registrar_evento_consulta(nome: str, id_raca: Optional[int], origem: str) -> None:
    with obter_conexao_banco() as conexao:
        conexao.execute(
            """
            INSERT INTO eventos_consulta_raca
            (nome_consultado, id_raca, encontrado, origem, consultado_em)
            VALUES (?, ?, ?, ?, ?)
            """,
            (nome, id_raca, 1 if id_raca else 0, origem, agora_utc_iso()),
        )


def executar_sincronizacao_etl() -> dict:
    inicio_execucao = agora_utc_iso()

    with obter_conexao_banco() as conexao:
        cursor = conexao.execute(
            (
                "INSERT INTO execucoes_sincronizacao "
                "(iniciado_em, status) VALUES (?, 'running')"
            ),
            (inicio_execucao,),
        )
        id_execucao = cursor.lastrowid

    logger.info("ETL start | id=%s | endpoint=/breeds", id_execucao)

    try:
        resposta = requests.get(
            f"{URL_BASE_API_DOG}/breeds",
            headers=montar_cabecalhos_api_dog(),
            timeout=30,
        )
        resposta.raise_for_status()
        racas = resposta.json()
        logger.info("ETL http | id=%s | status=%s | total=%s", id_execucao, resposta.status_code, len(racas))

        total = 0
        with obter_conexao_banco() as conexao:
            for item in racas:
                inserir_ou_atualizar_raca(conexao, item)
                total += 1

            conexao.execute(
                """
                UPDATE execucoes_sincronizacao
                SET finalizado_em = ?, status = 'succeeded',
                    total_registros = ?
                WHERE id = ?
                """,
                (agora_utc_iso(), total, id_execucao),
            )

        logger.info("ETL ok | id=%s | total=%s", id_execucao, total)
        return {
            "status": "succeeded",
            "totalRegistros": total,
            "idExecucao": id_execucao,
        }
    except Exception as exc:  # noqa: BLE001
        with obter_conexao_banco() as conexao:
            conexao.execute(
                """
                UPDATE execucoes_sincronizacao
                SET finalizado_em = ?, status = 'failed', erro = ?
                WHERE id = ?
                """,
                (agora_utc_iso(), str(exc), id_execucao),
            )
        logger.warning("ETL falhou | id=%s | erro=%s", id_execucao, exc)
        raise


@app.on_event("startup")
def iniciar_aplicacao() -> None:
    logar_configuracao_servico()
    inicializar_banco()
    logar_diagnostico_banco("apos_inicializar_banco")
    mesclar_banco_legado_se_necessario()
    logar_diagnostico_banco("apos_mesclar_banco_legado")
    sincronizar_racas_iniciais_se_necessario()
    logar_diagnostico_banco("apos_sincronizacao_inicial")

    scheduler.add_job(
        executar_sincronizacao_etl,
        "cron",
        hour=HORA_SINCRONIZACAO_UTC,
        minute=0,
    )
    scheduler.start()
    logger.info("Startup | scheduler=on | %02d:00Z", HORA_SINCRONIZACAO_UTC)


@app.on_event("shutdown")
def encerrar_aplicacao() -> None:
    logger.info("Shutdown | app=off")
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Shutdown | scheduler=off")


@app.get("/health")
def saude_servico() -> dict:
    return {"status": "ok", "service": "dados-py", "time": agora_utc_iso()}


@app.post("/etl/sync/racas")
def sincronizar_racas() -> dict:
    try:
        return executar_sincronizacao_etl()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Falha no ETL de racas: {exc}",
        ) from exc


@app.post("/racas/cadastrar")
def cadastrar_raca(dados: RacaCadastroRequest) -> dict:
    """Registra uma raça na base local. Enriquece com TheDogAPI se disponível."""
    nome_limpo = dados.nome.strip()
    candidatos_consulta = gerar_candidatos_consulta_externa(nome_limpo)
    nome_consulta_externa = candidatos_consulta[0] if candidatos_consulta else nome_limpo
    logger.debug(
        "Raca cadastro -> nome=%s | ext=%s | candidatos=%s",
        nome_limpo,
        nome_consulta_externa,
        candidatos_consulta,
    )
    if len(nome_limpo) < 2:
        raise HTTPException(
            status_code=400,
            detail="Nome da raca deve ter pelo menos 2 caracteres.",
        )

    linha_local = buscar_raca_local_por_nome(nome_limpo)
    if linha_local and linha_possui_dados_uteis(linha_local):
        resposta = montar_resposta_info_raca(nome_limpo, linha_local, linha_local["origem"])
        resposta["mensagem"] = "Raca ja presente na base local."
        logger.info(
            "Raca cadastro | src=local | nome=%s | ext=%s",
            nome_limpo,
            linha_local["nome"],
        )
        return resposta

    grupo = dados.grupo
    temperamento = dados.temperamento
    expectativa_vida = dados.expectativa_vida
    peso = dados.peso
    altura = dados.altura
    origem = "usuario"
    fonte_cadastro = "usuario"

    linha_externa = None
    for candidato in candidatos_consulta or [nome_limpo]:
        linha_externa = buscar_raca_por_nome(candidato)
        if linha_externa is not None:
            break

    if linha_externa:
        grupo = grupo or linha_externa["grupo"]
        temperamento = temperamento or linha_externa["temperamento"]
        expectativa_vida = expectativa_vida or linha_externa["expectativa_vida"]
        peso = peso or linha_externa["peso_metrico"]
        altura = altura or linha_externa["altura_metrica"]
        origem = linha_externa["origem"]
        fonte_cadastro = "raca_externa"
    else:
        linha_cache = None
        for candidato in candidatos_consulta or [nome_limpo]:
            linha_cache = buscar_raca_no_cache_por_nome(candidato)
            if linha_cache is not None:
                break

        if linha_cache:
            grupo = grupo or linha_cache["grupo"]
            temperamento = temperamento or linha_cache["temperamento"]
            expectativa_vida = expectativa_vida or linha_cache["expectativa_vida"]
            peso = peso or linha_cache["peso_metrico"]
            altura = altura or linha_cache["altura_metrica"]
            origem = "TheDogAPI"
            fonte_cadastro = "dogapi_cache"

            with obter_conexao_banco() as conexao:
                salvar_raca_externa(
                    conexao,
                    montar_dados_raca_externa_da_linha(linha_cache),
                )
        else:
            resultado_api = None
            for candidato in candidatos_consulta or [nome_consulta_externa]:
                resultado_api = buscar_no_dogapi_por_nome(candidato)
                if resultado_api:
                    break
            if resultado_api:
                grupo = grupo or normalizar_texto(resultado_api.get("breed_group"))
                temperamento = temperamento or normalizar_texto(resultado_api.get("temperament"))
                expectativa_vida = expectativa_vida or normalizar_texto(resultado_api.get("life_span"))
                peso = peso or normalizar_texto((resultado_api.get("weight") or {}).get("metric"))
                altura = altura or normalizar_texto((resultado_api.get("height") or {}).get("metric"))
                origem = "TheDogAPI"
                fonte_cadastro = "dogapi_on_demand"

    grupo_final = grupo or "Nao informado"
    temperamento_final = temperamento or "Nao informado"
    expectativa_final = expectativa_vida or "Nao informado"
    peso_final = peso or "Nao informado"
    altura_final = altura or "Nao informado"

    with obter_conexao_banco() as conexao:
        salvar_raca_local(conexao, nome_limpo, grupo_final, temperamento_final, expectativa_final, peso_final, altura_final, origem)

    resposta = {
        "mensagem": "Raca cadastrada na base local com sucesso.",
        "nome": nome_limpo,
        "nomeExterno": nome_consulta_externa if nome_consulta_externa != nome_limpo else nome_limpo,
        "grupo": grupo_final,
        "temperamento": temperamento_final,
        "expectativaVida": expectativa_final,
        "peso": peso_final,
        "altura": altura_final,
        "fonte": origem,
    }
    if linha_externa:
        resposta.update(montar_resposta_info_raca(nome_limpo, linha_externa, origem))
        resposta["mensagem"] = "Raca cadastrada na base local com sucesso."
    logger.info(
        "Raca cadastro | src=%s | nome=%s | ext=%s",
        fonte_cadastro,
        nome_limpo,
        resposta["nomeExterno"],
    )
    return resposta


def consultar_info_raca(nome: str) -> dict:
    nome_limpo = nome.strip()
    candidatos_consulta = gerar_candidatos_consulta_externa(nome_limpo)
    nome_consulta_externa = candidatos_consulta[0] if candidatos_consulta else nome_limpo
    logger.debug(
        "Raca consulta -> nome=%s | limpo=%s | ext=%s | candidatos=%s | chave=%s",
        nome,
        nome_limpo,
        nome_consulta_externa,
        candidatos_consulta,
        normalizar_chave_raca(nome_limpo),
    )
    if len(nome_limpo) < 2:
        logger.warning("Raca rejeitada | nome=%s | motivo=curto", nome)
        raise HTTPException(
            status_code=400,
            detail="Nome da raca deve ter pelo menos 2 caracteres.",
        )

    linha_local = None
    for candidato in candidatos_consulta or [nome_limpo]:
        linha_local = buscar_raca_local_por_nome(candidato)
        if linha_local is not None:
            break
    if linha_local and linha_possui_dados_uteis(linha_local):
        registrar_evento_consulta(nome_limpo, None, "local")
        logger.info(
            "Raca | src=local | nome=%s | ext=%s",
            nome_limpo,
            linha_local["nome"],
        )
        return montar_resposta_info_raca(nome_limpo, linha_local, linha_local["origem"])
    logger.debug(
        "Raca miss | src=local | nome=%s | candidatos=%s",
        nome_limpo,
        candidatos_consulta,
    )

    linha = None
    for candidato in candidatos_consulta or [nome_limpo]:
        linha = buscar_raca_por_nome(candidato)
        if linha is not None:
            break
    if linha:
        registrar_evento_consulta(nome_limpo, linha["id_raca"], "raca_externa")
        logger.info(
            "Raca | src=raca_externa | nome=%s | ext=%s | id=%s",
            nome_limpo,
            linha["nome"],
            linha["id_raca"],
        )
        return montar_resposta_info_raca(nome_limpo, linha, linha["origem"])
    logger.debug(
        "Raca miss | src=raca_externa | nome=%s",
        nome_limpo,
    )

    linha_cache = None
    for candidato in candidatos_consulta or [nome_limpo]:
        linha_cache = buscar_raca_no_cache_por_nome(candidato)
        if linha_cache is not None:
            break
    if linha_cache:
        with obter_conexao_banco() as conexao:
            salvar_raca_externa(
                conexao,
                montar_dados_raca_externa_da_linha(linha_cache),
            )

        registrar_evento_consulta(nome_limpo, linha_cache["id_raca"], "dogapi_cache")
        logger.info(
            "Raca | src=dogapi_cache | nome=%s | ext=%s | id=%s",
            nome_limpo,
            linha_cache["nome"],
            linha_cache["id_raca"],
        )
        return montar_resposta_info_raca(nome_limpo, linha_cache, linha_cache["origem"])
    logger.debug("Raca miss | src=dogapi_cache | nome=%s", nome_limpo)

    resultado_api = None
    logger.info(
        "Raca | src=dogapi_on_demand | nome=%s | candidatos=%s",
        nome_limpo,
        candidatos_consulta or [nome_consulta_externa],
    )
    for candidato in candidatos_consulta or [nome_consulta_externa]:
        resultado_api = buscar_no_dogapi_por_nome(candidato)
        if resultado_api:
            break
    if resultado_api:
        grupo = normalizar_texto(resultado_api.get("breed_group"))
        temperamento = normalizar_texto(resultado_api.get("temperament"))
        expectativa = normalizar_texto(resultado_api.get("life_span"))
        peso = normalizar_texto((resultado_api.get("weight") or {}).get("metric"))
        altura = normalizar_texto((resultado_api.get("height") or {}).get("metric"))
        nome_api = normalizar_texto(resultado_api.get("name"), padrao=nome_limpo)

        with obter_conexao_banco() as conexao:
            salvar_raca_local(conexao, nome_limpo, grupo, temperamento, expectativa, peso, altura, "TheDogAPI")

        registrar_evento_consulta(nome_limpo, resultado_api.get("id"), "dogapi_on_demand")
        logger.info(
            "Raca | src=dogapi_on_demand | nome=%s | ext=%s | id=%s",
            nome_limpo,
            nome_api,
            resultado_api.get("id"),
        )
        return montar_resposta_info_raca(
            nome_limpo,
            montar_dados_raca_externa_do_item(resultado_api),
            "TheDogAPI",
        )

    if linha_local:
        registrar_evento_consulta(nome_limpo, None, "local_sem_enriquecimento")
        logger.info(
            "Raca | src=local_sem_enriquecimento | nome=%s | ext=%s",
            nome_limpo,
            linha_local["nome"],
        )
        return montar_resposta_info_raca(nome_limpo, linha_local, linha_local["origem"])

    registrar_evento_consulta(nome_limpo, None, "nao_encontrada")
    logger.warning(
        "Raca miss | src=final | nome=%s | ext=%s | chave=%s",
        nome_limpo,
        nome_consulta_externa,
        normalizar_chave_raca(nome_limpo),
    )
    raise HTTPException(
        status_code=404,
        detail="Raca nao encontrada na base externa local.",
    )


@app.get("/racas/info")
def obter_info_raca_por_query(nome: str) -> dict:
    return consultar_info_raca(nome)


@app.get("/racas/info/{nome}")
def obter_info_raca_por_path(nome: str) -> dict:
    return consultar_info_raca(nome)


@app.get("/dogapi/cache")
def listar_cache_dogapi(limit: int = 50, offset: int = 0) -> dict:
    with obter_conexao_banco() as conexao:
        total = conexao.execute(
            "SELECT COUNT(*) AS total FROM racas_externas"
        ).fetchone()["total"]

        linhas = conexao.execute(
            """
            SELECT id_raca AS id_dog_api, nome, nome_original, nome_alternativo, grupo, temperamento,
                   expectativa_vida, peso_metrico, peso_imperial,
                   altura_metrica, altura_imperial,
                   origem, origem_raca, criado_para, referencia_imagem_id, imagem_url, descricao, atualizado_em
            FROM racas_externas
            ORDER BY nome ASC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "dados": [dict(linha) for linha in linhas],
    }


@app.get("/dogapi/cache/{id_dog_api}")
def buscar_cache_dogapi_por_id(id_dog_api: int) -> dict:
    with obter_conexao_banco() as conexao:
        linha = conexao.execute(
            "SELECT * FROM racas_externas WHERE id_raca = ?",
            (id_dog_api,),
        ).fetchone()

    if linha is None:
        raise HTTPException(status_code=404, detail="Raca nao encontrada no cache da Dog API.")
    return dict(linha)


@app.get("/analytics/resumo")
def resumo_analytics() -> dict:
    with obter_conexao_banco() as conexao:
        total_racas_externas = conexao.execute(
            "SELECT COUNT(*) AS total FROM racas_externas"
        ).fetchone()["total"]

        total_cache_dogapi = total_racas_externas

        total_racas_locais = conexao.execute(
            "SELECT COUNT(*) AS total FROM racas_locais"
        ).fetchone()["total"]

        total_consultas = conexao.execute(
            "SELECT COUNT(*) AS total FROM eventos_consulta_raca"
        ).fetchone()["total"]

        racas_mais_consultadas = conexao.execute(
            """
            SELECT nome_consultado AS nome, COUNT(*) AS total
            FROM eventos_consulta_raca
            WHERE encontrado = 1
            GROUP BY nome_consultado
            ORDER BY total DESC
            LIMIT 10
            """
        ).fetchall()

        ultima_execucao = conexao.execute(
            """
                 SELECT id, iniciado_em, finalizado_em,
                     status, total_registros, erro
            FROM execucoes_sincronizacao
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    return {
        "totalRacas": total_racas_externas,
        "totalCacheDogApi": total_cache_dogapi,
        "totalRacasLocais": total_racas_locais,
        "totalConsultas": total_consultas,
        "topRacasConsultadas": [
            {
                "nome": linha["nome"],
                "total": linha["total"],
            }
            for linha in racas_mais_consultadas
        ],
        "ultimoSync": dict(ultima_execucao) if ultima_execucao else None,
    }
