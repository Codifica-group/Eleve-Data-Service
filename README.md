# Eleve-Data-Service

## Como rodar o Eleve-Data-Service

### 1. Ative o ambiente virtual (se já existir)
```bash
source .venv/bin/activate
```
ou crie um novo ambiente:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Instale as dependências
```bash
pip install -r requirements.txt
```

### 3. Execute o servidor FastAPI com Uvicorn
```bash
uvicorn main:app --host 0.0.0.0 --port 8001
```
O serviço estará disponível em http://localhost:8001.
```

Se precisar de variáveis de ambiente, crie um arquivo `.env` com as chaves necessárias (exemplo: CHAVE_API_DOG).