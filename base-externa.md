# Base Externa Centralizada (Backend + API Python)

## Objetivo
Centralizar no backend todo consumo de base externa de raca, removendo dependencias diretas no frontend.

## Arquitetura
1. Frontend (React Native) consome apenas API Java (`Backend-V2`).
2. Backend Java atua como gateway para dados externos de raca.
3. API Python (`Dados-Py`) concentra:
- ETL de racas externas (TheDogAPI -> banco local SQLite)
- consulta de informacao de raca (`/racas/info/{nome}`)
- metricas para analytics (`/analytics/resumo`)
4. Backend Java continua com IA de identificacao por foto via endpoint existente:
- `POST /api/racas/identificar`

## Base escolhida e APIs utilizadas
### Base externa principal
- Base escolhida: **TheDogAPI**
- Motivo da escolha:
  1. Catalogo estruturado de racas com campos relevantes para o produto.
  2. Boa cobertura para enriquecimento de dados (grupo, peso, altura, expectativa de vida, temperamento).
  3. Integracao simples para ETL e atualizacao periodica.

### API de IA (inferencia por imagem)
- API utilizada: endpoint interno Java com Gemini
- Endpoint: `POST /api/racas/identificar`
- Responsabilidade: identificar/sugerir raca com base na foto do pet.

### API de base externa (enriquecimento)
- API utilizada: servico Python `Dados-Py`, acessado via gateway Java
- Endpoint final consumido pelo app: `GET /api/racas/externa/info/{nome}`
- Responsabilidade: devolver dados estruturados da raca ja normalizados.

### Separacao de responsabilidade (defesa)
1. IA faz inferencia (o que a imagem parece ser).
2. Base faz enriquecimento (o que sabemos sobre a raca).
3. Essa separacao melhora qualidade de dados, reduz custo de token e facilita analytics.

## Endpoints de gateway no Backend Java
Novos endpoints em `Backend-V2`:
- `GET /api/racas/externa/info/{nome}`
- `GET /api/racas/externa/analytics/resumo`
- `POST /api/racas/externa/etl/sync`

Configuracao:
- `URL_DADOS_PY` em `application.properties` e `application-dev.properties`

## Componentes implementados
### Servico Python (`Dados-Py/main.py`)
- Banco local SQLite com tabelas:
  - `racas_externas`
  - `execucoes_sincronizacao`
  - `eventos_consulta_raca`
- ETL manual e agendado:
  - `POST /etl/sync/racas`
  - job diario (cron UTC) configurado por `HORA_SINCRONIZACAO_UTC`
- API de consulta de raca:
  - `GET /racas/info/{nome}`
- API de resumo analitico:
  - `GET /analytics/resumo`
- Healthcheck:
  - `GET /health`

### Frontend (Eleve-App)
- IA (foto -> sugestao de raca):
  - `src/api/racas/identificarRacaPorImagem.js`
- Base externa centralizada (via backend Java gateway):
  - `src/api/racas/buscarInfoRacaExterna.js`
- Tela de cadastro pet mostrando:
  - sugestoes da IA
  - confianca (quando disponivel)
  - dados externos da raca

## Variaveis de ambiente
Arquivo: `Dados-Py/.env.example`

Variaveis atuais (PT-BR):
- `CHAVE_API_DOG`
- `CAMINHO_BANCO_DADOS_PY`
- `DADOS_PY_HOST`
- `DADOS_PY_PORT`

Compatibilidade mantida com legado:
- `DOG_API_KEY`
- `DADOS_PY_DB_PATH`

## Como subir o servico Python
1. Entrar em `Dados-Py`
2. Criar/ativar venv
3. Instalar dependencias:
```bash
pip install -r requirements.txt
```
4. Copiar `.env.example` para `.env` e preencher `CHAVE_API_DOG`
5. Subir API:
```bash
bash run.sh
```

## Como subir via Docker Compose (recomendado)
Com o arquivo `compose.eleve-infra.yaml`:

1. Criar arquivo de ambiente do Python:
```bash
cp Dados-Py/.env.example Dados-Py/.env
```

2. Preencher `CHAVE_API_DOG` em `Dados-Py/.env`.

3. Subir stack completa com dados-py:
```bash
docker compose -f compose.eleve-infra.yaml up -d --build
```

4. Validar API Python:
```bash
curl http://localhost:8001/health
```

5. Rodar carga inicial ETL:
```bash
curl -X POST http://localhost:8001/etl/sync/racas
```

6. Testar consulta centralizada via backend Java:
```bash
curl http://localhost:8080/api/racas/externa/info/labrador
```

## Como executar o ETL inicial
Com a API no ar:
```bash
curl -X POST http://localhost:8001/etl/sync/racas
```

## Fluxo completo no produto
1. Usuario envia foto do pet.
2. Backend Java identifica raca com IA (`/api/racas/identificar`).
3. Front sugere racas ao usuario.
4. Front consulta dados da raca na API Python (`/racas/info/{nome}`), sem bater direto em base externa.
5. A consulta do app passa pelo backend Java (`/api/racas/externa/info/{nome}`).
6. API Python registra eventos de consulta para analytics.

## Justificativa para defesa (IA + Base)
### Por que usar IA e base juntas
1. A IA resolve percepcao: pela foto, ela estima a raca mais provavel.
2. A base resolve conhecimento estruturado: grupo, peso, altura, expectativa de vida e temperamento.
3. Isso evita misturar responsabilidades: IA para inferencia, base para dados confiaveis e padronizados.

### Beneficios tecnicos
1. Padronizacao: o mesmo nome de raca e os mesmos atributos em todas as telas.
2. Rastreabilidade: eventos de consulta ficam registrados para auditoria e BI.
3. Escalabilidade: a mesma resposta enriquecida pode ser reutilizada sem nova inferencia pesada.
4. Resiliencia: se a IA estiver indisponivel, ainda existe camada de dados para enriquecer fluxo manual.

### Beneficios de produto
1. Experiencia melhor: usuario recebe sugestao inteligente + informacao util da raca.
2. Decisao operacional: facilita ofertas de servicos por perfil de pet.
3. Evolucao analitica: cria base para metricas de acuracia e conversao.

## Custo e eficiencia
### Custo de IA (tokens)
1. Cada chamada multimodal da IA consome token/credito.
2. Quanto mais informacao a IA gerar por chamada, maior o custo unitario.
3. Repetir chamadas para os mesmos casos aumenta custo sem agregar valor proporcional.

### Custo com base enriquecida
1. Consulta em base local custa muito menos que inferencia multimodal repetida.
2. ETL diario concentra custo em lote e reduz chamadas online em tempo real.
3. Dados reutilizaveis diminuem latencia e tornam gasto previsivel.

### Estrategia adotada para otimizar custo
1. IA apenas para identificar raca na imagem (etapa de alto valor cognitivo).
2. Base externa/local para enriquecer atributos (etapa barata e repetivel).
3. Gateway Java para centralizar controle de chamadas e observabilidade.
4. Eventos de analytics para medir uso real e calibrar custo-beneficio.

### Mensagem executiva (resumo)
Usamos IA somente onde ela agrega mais valor (identificar raca pela foto) e usamos base estruturada para enriquecer dados com baixo custo e alta consistencia.

## Analytics e ETL (base recomendada)
### Camada de entrada
- TheDogAPI como fonte externa de referencia de raca.

### Camada operacional
- SQLite local no `Dados-Py` para MVP.

### Camada analitica sugerida (proximo passo)
1. Migrar de SQLite para PostgreSQL analitico (ou ClickHouse/BigQuery).
2. ETL incremental com Airbyte ou Meltano.
3. Transformacoes com dbt.
4. Dashboard com Power BI (Metabase como alternativa).

## Metricas sugeridas
1. Total de consultas de raca por periodo.
2. Top 10 racas mais consultadas.
3. Taxa de sucesso de correspondencia de raca (`encontrado`).
4. Latencia e estabilidade do job ETL.

## Decisoes tecnicas
1. Mudanca minima no backend Java: reutilizamos endpoint existente de IA.
2. Centralizacao da base externa no backend Python.
3. Frontend desacoplado de fornecedores externos de dados.
4. Nomenclatura padronizada em PT-BR no servico Python.

## Proximos passos opcionais
1. Adicionar autenticacao nos endpoints do `Dados-Py`.
2. Expor endpoint para historico de ETL por data.
3. Persistir no backend Java a raca sugerida pela IA e a raca final escolhida.
4. Publicar dashboard inicial no Power BI com `analytics/resumo`.
