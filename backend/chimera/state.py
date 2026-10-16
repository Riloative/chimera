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
from .web.browser import BrowserClient
from .web.egress import EgressProxy
from .web.fetcher import SafeFetcher, make_client
from .web.service import WebService


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
    web: WebService

    @classmethod
    def build(cls, config: Config, client: httpx.AsyncClient, web_client: httpx.AsyncClient | None = None) -> "AppState":
        db = Database(config.db_path, backups_dir=config.backups_dir)
        box = SecretBox(config.encryption_key)
        registry = ProviderRegistry(db, client, box)
        fetcher = SafeFetcher(web_client or make_client(config.web_proxy or None), via_proxy=bool(config.web_proxy),
                              verbose_log=config.web_verbose_log)
        web = WebService(db, config, box, fetcher)
        if config.browser_configured:
            web.browser = BrowserClient(config.browser_url, config.browser_secret, EgressProxy(resolver=fetcher.resolver))
        settings = SettingsStore(db)
        conversations = ConversationStore(db)
        files = FileStore(db, config.uploads_dir)
        knowledge = KnowledgeBase(db, registry)
        chat = ChatEngine(db, conversations, registry, settings, files, knowledge, web)
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
            web=web,
        )


def get_state(request: Request) -> AppState:
    return request.app.state.chimera
