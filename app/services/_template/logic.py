from app.services._template.schemas import EchoRequest, EchoResponse


def echo(payload: EchoRequest) -> EchoResponse:
    return EchoResponse(echo=payload.message)
