import os
import mysql.connector
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()

MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", 3306))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "root")
MYSQL_DB = os.getenv("MYSQL_DB", "Eleve")

@contextmanager
def obter_conexao_mysql():
    conn = mysql.connector.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB
    )
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

# Exemplo de uso:
if __name__ == "__main__":
    with obter_conexao_mysql() as conn:
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        print(cursor.fetchall())
