import json
import logging

from autoescrow.core.logging import JsonFormatter, request_id_ctx


def test_json_formatter_includes_request_id_and_extra_fields() -> None:
    record = logging.LogRecord(
        name="autoescrow.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="processed %s",
        args=("request",),
        exc_info=None,
    )
    record.http_status = 200

    token = request_id_ctx.set("request-123")
    try:
        payload = json.loads(JsonFormatter().format(record))
    finally:
        request_id_ctx.reset(token)

    assert payload["level"] == "INFO"
    assert payload["logger"] == "autoescrow.test"
    assert payload["message"] == "processed request"
    assert payload["request_id"] == "request-123"
    assert payload["http_status"] == 200
    assert "timestamp" in payload
