from __future__ import annotations

import json

from sqlalchemy.orm import Session, sessionmaker

from pdf_trans.web.config import WebSettings
from pdf_trans.web.mineru_connection import MinerUConnection
from pdf_trans.web.models import ApplicationSetting, utc_now


class MinerUSettingsRepository:
    def __init__(self, sessions: sessionmaker[Session], defaults: WebSettings) -> None:
        self._sessions = sessions
        self._defaults = {
            "url": defaults.mineru_url,
            "gpustack_model": defaults.mineru_gpustack_model,
            "api_key": defaults.mineru_api_key,
        }

    def _stored(self) -> dict | None:
        with self._sessions() as session:
            row = session.get(ApplicationSetting, "mineru")
            return json.loads(row.value) if row else None

    def get(self) -> MinerUConnection:
        stored = self._stored() or {}
        return MinerUConnection(**{
            key: self._defaults[key] if stored.get(key) is None else stored[key]
            for key in self._defaults
        })

    def public_settings(self) -> dict:
        current = self.get()
        return {
            "url": current.url,
            "gpustack_model": current.gpustack_model,
            "has_api_key": bool(current.api_key),
            "is_overridden": self._stored() is not None,
        }

    def draft(self, *, url: str, gpustack_model: str = "mineru4", api_key: str = "") -> MinerUConnection:
        stored = self._stored() or {}
        key = api_key.strip() or stored.get("api_key") or self._defaults["api_key"]
        return MinerUConnection(url=url, gpustack_model=gpustack_model, api_key=key)

    def save(self, *, url: str, gpustack_model: str, api_key: str = "") -> None:
        with self._sessions.begin() as session:
            row = session.get(ApplicationSetting, "mineru", with_for_update=True)
            stored = json.loads(row.value) if row else {}
            key_override = api_key.strip() or stored.get("api_key")
            current = MinerUConnection(
                url=url,
                gpustack_model=gpustack_model,
                api_key=key_override or self._defaults["api_key"],
            )
            value = json.dumps({
                "url": current.url,
                "gpustack_model": current.gpustack_model,
                "api_key": key_override,
            })
            if row is None:
                session.add(ApplicationSetting(key="mineru", value=value, updated_at=utc_now()))
            else:
                row.value = value
                row.updated_at = utc_now()

    def reset(self) -> None:
        with self._sessions.begin() as session:
            row = session.get(ApplicationSetting, "mineru", with_for_update=True)
            if row is not None:
                session.delete(row)
