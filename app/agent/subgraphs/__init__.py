"""两个业务子图：Document Understanding 与 Business Application。"""
from app.agent.subgraphs.business_application import (
    application_app,
    application_state_t,
    build_application_app,
)
from app.agent.subgraphs.document_understanding import (
    build_document_app,
    document_app,
    document_state_t,
)

__all__ = [
    "application_app",
    "application_state_t",
    "build_application_app",
    "build_document_app",
    "document_app",
    "document_state_t",
]
