from fastapi import APIRouter, status

from app.services._template import logic
from app.services._template.schemas import EchoRequest, EchoResponse

router = APIRouter(prefix="/services/_template", tags=["_template"])


@router.post(
    "/echo",
    response_model=EchoResponse,
    status_code=status.HTTP_200_OK,
    summary="Renvoie le message reçu (endpoint de démonstration).",
)
def post_echo(payload: EchoRequest) -> EchoResponse:
    return logic.echo(payload)
