from datetime import datetime

class ISO8601Datetime(str):
    @classmethod
    def from_datetime(cls, dt: datetime, timespec: str = ...) -> ISO8601Datetime: ...

class SiweMessage:
    scheme: str | None
    domain: str
    address: str
    uri: str
    version: str
    chain_id: int
    issued_at: ISO8601Datetime
    nonce: str
    statement: str | None
    expiration_time: ISO8601Datetime | None
    not_before: ISO8601Datetime | None
    request_id: str | None
    resources: list[str] | None

    def __init__(
        self,
        *,
        scheme: str | None = ...,
        domain: str,
        address: str,
        uri: str,
        version: str,
        chain_id: int,
        issued_at: str,
        nonce: str,
        statement: str | None = ...,
        expiration_time: str | None = ...,
        not_before: str | None = ...,
        request_id: str | None = ...,
        resources: list[str] | None = ...,
    ) -> None: ...
    @classmethod
    def from_message(cls, message: str, abnf: bool = ...) -> SiweMessage: ...
    def prepare_message(self) -> str: ...

class VerificationError(Exception): ...
class DomainMismatch(VerificationError): ...
class NonceMismatch(VerificationError): ...
class InvalidSignature(VerificationError): ...
class ExpiredMessage(VerificationError): ...
class NotYetValidMessage(VerificationError): ...
class MalformedSession(VerificationError): ...
