"""Application container wiring services together."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import Request

from .config import Config
from .crypto import SecretBox
from .db import Database
from .services.auth import AuthService
from .services.chat import ChatEngine
from .services.conversations import ConversationStore
from .services.files import FileStore
from .services.knowledge import KnowledgeBase
from .services.providers import ProviderRegistry
from .services.settings import SettingsStore


@dataclass
class AppState:
    config: Config
    db: Database
    client: httpx.AsyncClient
    registry: ProviderRegistry
    settings: SettingsStore
    conversations: ConversationStore
    files: FileStore
    knowledge: KnowledgeBase
    chat: ChatEngine
    auth: AuthService

    @classmethod
    def build(cls, config: Config, client: httpx.AsyncClient) -> "AppState":
        db = Database(config.db_path, backups_dir=config.backups_dir)
        registry = ProviderRegistry(db, client, SecretBox(config.encryption_key))
        settings = SettingsStore(db)
        conversations = ConversationStore(db)
        files = FileStore(db, config.uploads_dir)
        knowledge = KnowledgeBase(db, registry)
        chat = ChatEngine(db, conversations, registry, settings, files, knowledge)
        return cls(
            config=config,
            db=db,
            client=client,
            registry=registry,
            settings=settings,
            conversations=conversations,
            files=files,
            knowledge=knowledge,
            chat=chat,
            auth=AuthService(db, config),
        )


def get_state(request: Request) -> AppState:
    return request.app.state.chimera
