from pydantic import BaseModel, Field


class EchoRequest(BaseModel):
    message: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Texte à renvoyer en écho.",
    )


class EchoResponse(BaseModel):
    echo: str = Field(..., description="Texte renvoyé tel quel par le service.")
