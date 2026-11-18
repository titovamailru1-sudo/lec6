"""Однопользовательский REST-сервер журнала с хранилищем SQLite."""

import argparse
import hmac
import json
import os
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ledger import merge, validate
from sync_api import BODY_LIMIT, SYNC_PATH


def init_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, body TEXT NOT NULL)")


def store_snapshot(path, changes):
    with closing(sqlite3.connect(path, timeout=10)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        current = {record_id: json.loads(body) for record_id, body in connection.execute("SELECT id, body FROM operations")}
        combined = merge(current, changes)
        response = encode({"records": list(combined.values())})
        if len(response) > BODY_LIMIT:
            raise ValueError("Журнал превышает 8 МБ.")
        connection.executemany(
            "INSERT OR REPLACE INTO operations (id, body) VALUES (?, ?)",
            [(key, json.dumps(value, ensure_ascii=False)) for key, value in combined.items()
             if current.get(key) != value],
        )
    return response


def encode(payload):
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, encode({"status": "ok"}))
        else:
            self.reply(404, encode({"error": "Endpoint not found"}))

    def do_POST(self):
        if self.path != SYNC_PATH:
            self.reply(404, encode({"error": "Endpoint not found"}))
            return
        actual = self.headers.get("Authorization", "").encode("utf-8")
        expected = f"Bearer {self.server.token}".encode("utf-8")
        if not hmac.compare_digest(actual, expected):
            self.reply(401, encode({"error": "Unauthorized"}))
            return
        self.connection.settimeout(10)
        try:
            size = int(self.headers.get("Content-Length", 0))
            if size > BODY_LIMIT:
                self.reply(413, encode({"error": "Request too large"}))
                return
            if size <= 0:
                raise ValueError
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict) or not isinstance(data.get("records"), list):
                raise ValueError
            records = [validate(item) for item in data["records"]]
            snapshot = store_snapshot(self.server.db_path, records)
        except (UnicodeError, ValueError, TimeoutError):
            self.reply(400, encode({"error": "Invalid snapshot"}))
            return
        except sqlite3.Error:
            self.reply(503, encode({"error": "Database unavailable"}))
            return
        self.reply(200, snapshot)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=Path("server-data/ledger.sqlite3"))
    args = parser.parse_args()
    token = os.environ.get("FINANCE_SYNC_TOKEN", "")
    if len(token) < 16 or any(not 33 <= ord(char) <= 126 for char in token):
        parser.error("Задайте FINANCE_SYNC_TOKEN из 16 или более непробельных ASCII-символов.")
    init_db(args.db)
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        server.token, server.db_path = token, args.db
        print(f"REST API: http://{args.host}:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
