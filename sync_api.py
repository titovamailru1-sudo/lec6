"""Контракт обмена снимками журнала по REST API."""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ledger import validate

SYNC_PATH = "/api/v1/sync"
BODY_LIMIT = 8 * 1024 * 1024


def endpoint(url):
    url = url.strip().rstrip("/")
    try:
        parts = urlsplit(url)
        parts.port
    except ValueError as exc:
        raise ValueError("Введите корректный адрес сервера.") from exc
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Введите корректный адрес сервера.")
    if parts.scheme == "https":
        return url
    if parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"}:
        return url
    raise ValueError("Для удалённого сервера нужен адрес HTTPS.")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def exchange(url, token, records):
    body = json.dumps({"records": records}, ensure_ascii=False).encode("utf-8")
    if len(body) > BODY_LIMIT:
        raise ValueError("Журнал превышает ограничение запроса в 8 МБ.")
    request = Request(
        endpoint(url) + SYNC_PATH, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with build_opener(NoRedirect).open(request, timeout=7) as response:
            raw = response.read(BODY_LIMIT + 1)
    except HTTPError as exc:
        detail = "Проверьте токен доступа." if exc.code == 401 else f"Сервер вернул HTTP {exc.code}."
        raise ValueError(detail) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError("Сервер недоступен. Проверьте адрес и подключение.") from exc
    try:
        if len(raw) > BODY_LIMIT:
            raise ValueError
        rows = json.loads(raw)["records"]
        if not isinstance(rows, list):
            raise ValueError
        return [validate(row) for row in rows]
    except (KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise ValueError("Ответ сервера не соответствует формату журнала.") from exc
