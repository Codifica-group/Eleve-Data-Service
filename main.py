import os
import sqlite3
import unicodedata
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

CHAVE_API_DOG = os.getenv(
    "CHAVE_API_DOG",
    os.getenv("DOG_API_KEY", ""),
).strip()
CAMINHO_BANCO_DADOS = os.getenv(
    "CAMINHO_BANCO_DADOS_PY",
    os.getenv("DADOS_PY_DB_PATH", "./dados_py.db"),
)
URL_BASE_API_DOG = "https://api.thedogapi.com/v1"
HORA_SINCRONIZACAO_UTC = int(os.getenv("HORA_SINCRONIZACAO_UTC", "3"))

logger = logging.getLogger("eleve.data_service")
logger.setLevel(getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO))

RACA_DE_PARA = {
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
    "maltes": "Maltese",
    "shih tzu": "Shih Tzu",
    "shihtzu": "Shih Tzu",
    "pinscher": "Miniature Pinscher",
    "yorkshire": "Yorkshire Terrier",
    "labrador": "Labrador Retriever",
    "golden": "Golden Retriever",
    "beagle": "Beagle",
    "border collie": "Border Collie",
    "pug": "Pug",
    "rottweiler": "Rottweiler",
    "husky siberiano": "Siberian Husky",
    "doberman": "Doberman Pinscher",
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

    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001
        duracao_ms = round((time.perf_counter() - inicio) * 1000)
        logger.exception(
            "REQ falhou metodo=%s path=%s query=%s cliente=%s duracao_ms=%s detalhe=%s",
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
        "REQ metodo=%s path=%s query=%s cliente=%s status=%s duracao_ms=%s",
        request.method,
        request.url.path,
        query or "-",
        cliente,
        response.status_code,
        duracao_ms,
    )
    return response


def agora_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def obter_conexao_banco():
    conexao = sqlite3.connect(CAMINHO_BANCO_DADOS)
    conexao.row_factory = sqlite3.Row
    try:
        yield conexao
        conexao.commit()
    finally:
        conexao.close()


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
                grupo TEXT,
                temperamento TEXT,
                expectativa_vida TEXT,
                peso_metrico TEXT,
                altura_metrica TEXT,
                origem TEXT DEFAULT 'TheDogAPI',
                atualizado_em TEXT NOT NULL
            )
            """
        )

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
    nome_limpo = str(nome or "").strip()
    if not nome_limpo:
        return ""
    return RACA_DE_PARA.get(normalizar_chave_raca(nome_limpo), nome_limpo)


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

    conexao.execute(
        """
        INSERT INTO racas_externas (
            id_raca, nome, grupo, temperamento, expectativa_vida,
            peso_metrico, altura_metrica, origem, atualizado_em
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'TheDogAPI', ?)
        ON CONFLICT(id_raca) DO UPDATE SET
            nome=excluded.nome,
            grupo=excluded.grupo,
            temperamento=excluded.temperamento,
            expectativa_vida=excluded.expectativa_vida,
            peso_metrico=excluded.peso_metrico,
            altura_metrica=excluded.altura_metrica,
            origem=excluded.origem,
            atualizado_em=excluded.atualizado_em
        """,
        (
            id_raca,
            normalizar_texto(item.get("name"), padrao="Raca sem nome"),
            normalizar_texto(item.get("breed_group")),
            normalizar_texto(item.get("temperament")),
            normalizar_texto(item.get("life_span")),
            normalizar_texto((item.get("weight") or {}).get("metric")),
            normalizar_texto((item.get("height") or {}).get("metric")),
            agora_utc_iso(),
        ),
    )


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
    return buscar_melhor_correspondencia(nome, linhas)


def buscar_raca_por_nome(nome: str) -> Optional[sqlite3.Row]:
    with obter_conexao_banco() as conexao:
        linhas = conexao.execute(
            """
            SELECT *
            FROM racas_externas
            ORDER BY nome ASC
            """
        ).fetchall()
    return buscar_melhor_correspondencia(nome, linhas)


def buscar_no_dogapi_por_nome(nome: str) -> Optional[dict]:
    """Busca on-demand no TheDogAPI. Persiste no dogapi_cache e retorna None se não encontrado."""
    logger.info("DOGAPI consulta iniciada nome_consulta='%s'", nome)
    try:
        resposta = requests.get(
            f"{URL_BASE_API_DOG}/breeds/search",
            params={"q": nome},
            headers=montar_cabecalhos_api_dog(),
            timeout=10,
        )
        resposta.raise_for_status()
        resultados = resposta.json()
        if not resultados:
            logger.info("DOGAPI consulta sem resultado nome_consulta='%s'", nome)
            return None
        item = resultados[0]
        with obter_conexao_banco() as conexao:
            salvar_no_cache_dogapi(conexao, item)
        logger.info(
            "DOGAPI consulta concluida nome_consulta='%s' nome_encontrado='%s' id='%s'",
            nome,
            item.get("name"),
            item.get("id"),
        )
        return item
    except Exception as exc:  # noqa: BLE001
        logger.warning("DOGAPI consulta falhou nome_consulta='%s' detalhe='%s'", nome, exc)
        return None


def registrar_evento_consulta(nome: str, id_raca: Optional[int]) -> None:
    with obter_conexao_banco() as conexao:
        conexao.execute(
            """
            INSERT INTO eventos_consulta_raca
            (nome_consultado, id_raca, encontrado, origem, consultado_em)
            VALUES (?, ?, ?, 'TheDogAPI', ?)
            """,
            (nome, id_raca, 1 if id_raca else 0, agora_utc_iso()),
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

    try:
        resposta = requests.get(
            f"{URL_BASE_API_DOG}/breeds",
            headers=montar_cabecalhos_api_dog(),
            timeout=30,
        )
        resposta.raise_for_status()
        racas = resposta.json()

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
        raise


@app.on_event("startup")
def iniciar_aplicacao() -> None:
    inicializar_banco()

    scheduler.add_job(
        executar_sincronizacao_etl,
        "cron",
        hour=HORA_SINCRONIZACAO_UTC,
        minute=0,
    )
    scheduler.start()


@app.on_event("shutdown")
def encerrar_aplicacao() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


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
    nome_consulta_externa = traduzir_raca_para_consulta_externa(nome_limpo)
    if len(nome_limpo) < 2:
        raise HTTPException(
            status_code=400,
            detail="Nome da raca deve ter pelo menos 2 caracteres.",
        )

    linha_local = buscar_raca_local_por_nome(nome_limpo)
    if linha_local and linha_possui_dados_uteis(linha_local):
        return {
            "mensagem": "Raca ja presente na base local.",
            "nome": linha_local["nome"],
            "nomeExterno": nome_consulta_externa if nome_consulta_externa != linha_local["nome"] else linha_local["nome"],
            "grupo": linha_local["grupo"],
            "temperamento": linha_local["temperamento"],
            "expectativaVida": linha_local["expectativa_vida"],
            "peso": linha_local["peso"],
            "altura": linha_local["altura"],
            "fonte": linha_local["origem"],
        }

    grupo = dados.grupo
    temperamento = dados.temperamento
    expectativa_vida = dados.expectativa_vida
    peso = dados.peso
    altura = dados.altura
    origem = "usuario"

    linha_externa = buscar_raca_por_nome(nome_limpo)
    if linha_externa is None and nome_consulta_externa != nome_limpo:
        linha_externa = buscar_raca_por_nome(nome_consulta_externa)

    if linha_externa:
        grupo = grupo or linha_externa["grupo"]
        temperamento = temperamento or linha_externa["temperamento"]
        expectativa_vida = expectativa_vida or linha_externa["expectativa_vida"]
        peso = peso or linha_externa["peso_metrico"]
        altura = altura or linha_externa["altura_metrica"]
        origem = "TheDogAPI"
    else:
        resultado_api = buscar_no_dogapi_por_nome(nome_consulta_externa)
        if resultado_api:
            grupo = grupo or normalizar_texto(resultado_api.get("breed_group"))
            temperamento = temperamento or normalizar_texto(resultado_api.get("temperament"))
            expectativa_vida = expectativa_vida or normalizar_texto(resultado_api.get("life_span"))
            peso = peso or normalizar_texto((resultado_api.get("weight") or {}).get("metric"))
            altura = altura or normalizar_texto((resultado_api.get("height") or {}).get("metric"))
            origem = "TheDogAPI"

    grupo_final = grupo or "Nao informado"
    temperamento_final = temperamento or "Nao informado"
    expectativa_final = expectativa_vida or "Nao informado"
    peso_final = peso or "Nao informado"
    altura_final = altura or "Nao informado"

    with obter_conexao_banco() as conexao:
        salvar_raca_local(conexao, nome_limpo, grupo_final, temperamento_final, expectativa_final, peso_final, altura_final, origem)

    return {
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


def consultar_info_raca(nome: str) -> dict:
    nome_limpo = nome.strip()
    nome_consulta_externa = traduzir_raca_para_consulta_externa(nome_limpo)
    logger.info(
        "RACA consulta recebida nome_original='%s' nome_limpo='%s' nome_externo='%s' chave_normalizada='%s'",
        nome,
        nome_limpo,
        nome_consulta_externa,
        normalizar_chave_raca(nome_limpo),
    )
    if len(nome_limpo) < 2:
        logger.warning("RACA consulta rejeitada nome_original='%s' motivo='nome_curto'", nome)
        raise HTTPException(
            status_code=400,
            detail="Nome da raca deve ter pelo menos 2 caracteres.",
        )

    linha_local = buscar_raca_local_por_nome(nome_limpo)
    if linha_local is None and nome_consulta_externa != nome_limpo:
        linha_local = buscar_raca_local_por_nome(nome_consulta_externa)
    if linha_local and linha_possui_dados_uteis(linha_local):
        registrar_evento_consulta(nome_limpo, None)
        logger.info(
            "RACA consulta concluida fonte='local' nome='%s' nome_externo='%s'",
            nome_limpo,
            linha_local["nome"],
        )
        return {
            "nome": nome_limpo,
            "nomeExterno": linha_local["nome"],
            "grupo": linha_local["grupo"],
            "temperamento": linha_local["temperamento"],
            "expectativaVida": linha_local["expectativa_vida"],
            "peso": linha_local["peso"],
            "altura": linha_local["altura"],
            "fonte": linha_local["origem"],
        }

    linha = buscar_raca_por_nome(nome_limpo)
    if linha is None and nome_consulta_externa != nome_limpo:
        linha = buscar_raca_por_nome(nome_consulta_externa)
    if linha:
        registrar_evento_consulta(nome_limpo, linha["id_raca"])
        logger.info(
            "RACA consulta concluida fonte='cache_externo' nome='%s' nome_externo='%s' race_id='%s'",
            nome_limpo,
            linha["nome"],
            linha["id_raca"],
        )
        return {
            "nome": nome_limpo,
            "nomeExterno": linha["nome"],
            "grupo": linha["grupo"],
            "temperamento": linha["temperamento"],
            "expectativaVida": linha["expectativa_vida"],
            "peso": linha["peso_metrico"],
            "altura": linha["altura_metrica"],
            "fonte": linha["origem"],
            "raceId": linha["id_raca"],
        }

    resultado_api = buscar_no_dogapi_por_nome(nome_consulta_externa)
    if resultado_api:
        grupo = normalizar_texto(resultado_api.get("breed_group"))
        temperamento = normalizar_texto(resultado_api.get("temperament"))
        expectativa = normalizar_texto(resultado_api.get("life_span"))
        peso = normalizar_texto((resultado_api.get("weight") or {}).get("metric"))
        altura = normalizar_texto((resultado_api.get("height") or {}).get("metric"))
        nome_api = normalizar_texto(resultado_api.get("name"), padrao=nome_limpo)

        with obter_conexao_banco() as conexao:
            salvar_raca_local(conexao, nome_limpo, grupo, temperamento, expectativa, peso, altura, "TheDogAPI")

        registrar_evento_consulta(nome_limpo, resultado_api.get("id"))
        logger.info(
            "RACA consulta concluida fonte='dogapi_on_demand' nome='%s' nome_externo='%s' race_id='%s'",
            nome_limpo,
            nome_api,
            resultado_api.get("id"),
        )
        return {
            "nome": nome_limpo,
            "nomeExterno": nome_api,
            "grupo": grupo,
            "temperamento": temperamento,
            "expectativaVida": expectativa,
            "peso": peso,
            "altura": altura,
            "fonte": "TheDogAPI",
            "raceId": resultado_api.get("id"),
        }

    if linha_local:
        registrar_evento_consulta(nome_limpo, None)
        logger.info(
            "RACA consulta concluida fonte='local_sem_enriquecimento' nome='%s' nome_externo='%s'",
            nome_limpo,
            linha_local["nome"],
        )
        return {
            "nome": nome_limpo,
            "nomeExterno": linha_local["nome"],
            "grupo": linha_local["grupo"],
            "temperamento": linha_local["temperamento"],
            "expectativaVida": linha_local["expectativa_vida"],
            "peso": linha_local["peso"],
            "altura": linha_local["altura"],
            "fonte": linha_local["origem"],
        }

    registrar_evento_consulta(nome_limpo, None)
    logger.warning(
        "RACA consulta sem resultado nome='%s' nome_externo='%s' chave_normalizada='%s'",
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
            "SELECT COUNT(*) AS total FROM dogapi_cache"
        ).fetchone()["total"]

        linhas = conexao.execute(
            """
            SELECT id_dog_api, nome, nome_alternativo, grupo, temperamento,
                   expectativa_vida, peso_metrico, peso_imperial,
                   altura_metrica, altura_imperial,
                   origem_raca, criado_para, referencia_imagem_id, atualizado_em
            FROM dogapi_cache
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
            "SELECT * FROM dogapi_cache WHERE id_dog_api = ?",
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

        total_cache_dogapi = conexao.execute(
            "SELECT COUNT(*) AS total FROM dogapi_cache"
        ).fetchone()["total"]

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
