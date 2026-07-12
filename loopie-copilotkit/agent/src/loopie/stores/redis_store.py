"""Redis live substrate for Loopie artifacts and streams."""

from __future__ import annotations

import json
import hashlib
import re
from typing import Any

import redis

from src.loopie.config import get_settings
from src.loopie.manifests import DEFAULT_PROJECT_ID


class RedisStore:
    def __init__(self, url: str | None = None, *, project_id: str = DEFAULT_PROJECT_ID) -> None:
        self.project_id = project_id
        self._prefix = f"loopie:{project_id}:"
        redis_url = url or get_settings().redis_url
        settings = get_settings()
        if (
            settings.hosted
            and not redis_url.lower().startswith("rediss://")
            and not settings.allow_insecure_redis
        ):
            raise RuntimeError(
                "Hosted Redis must use TLS via a rediss:// URL. To run hosted "
                "against a non-TLS Redis (e.g. a free-tier plan without TLS), "
                "set LOOPIE_ALLOW_INSECURE_REDIS=1 as an explicit, deliberate "
                "opt-out — traffic between the app and Redis will be "
                "unencrypted on the public internet."
            )
        self._client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
            max_connections=20,
        )
        # Blocking XREAD owns a separate small pool so it cannot starve ordinary
        # artifact/cache operations under reconnects or slow consumers.
        self._blocking_client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=20,
            health_check_interval=30,
            max_connections=4,
        )

    def ping(self) -> bool:
        return bool(self._client.ping())

    def _key(self, category: str, name: str) -> str:
        return f"{self._prefix}{category}:{name}"

    def set_memory(self, key: str, value: str, version: int = 1) -> None:
        self._client.set(self._key("memory", key), json.dumps({"value": value, "version": version}))

    def get_memory(self, key: str) -> dict[str, Any] | None:
        raw = self._client.get(self._key("memory", key))
        return json.loads(raw) if raw else None

    def set_routing_rules(self, rules: list[dict[str, Any]]) -> None:
        self._client.set(f"{self._prefix}routing:rules", json.dumps(rules))

    def get_routing_rules(self) -> list[dict[str, Any]]:
        raw = self._client.get(f"{self._prefix}routing:rules")
        return json.loads(raw) if raw else []

    def set_policy_rules(self, rules: list[dict[str, Any]]) -> None:
        self._client.set(f"{self._prefix}policy:rules", json.dumps(rules))

    def get_policy_rules(self) -> list[dict[str, Any]]:
        raw = self._client.get(f"{self._prefix}policy:rules")
        return json.loads(raw) if raw else []

    def set_config(self, key: str, value: Any) -> None:
        encoded = json.dumps(value, separators=(",", ":")) if isinstance(value, (dict, list)) else str(value)
        self._client.set(self._key("config", key), encoded)

    def get_config(self, key: str, default: str | None = None) -> str | None:
        return self._client.get(self._key("config", key)) or default

    def get_live_artifacts(self) -> dict[str, Any]:
        memory_raw = self.get_memory("policy:refund_window")
        memory = {}
        if memory_raw:
            memory["policy:refund_window"] = memory_raw.get("value", "")
        return {
            "memory": memory,
            "routing_rules": self.get_routing_rules(),
            "policy_rules": self.get_policy_rules(),
            "max_transitions": int(self.get_config("max_transitions", "6") or "6"),
        }

    def xadd(self, stream: str, fields: dict[str, Any]) -> str:
        payload = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in fields.items()}
        return self._client.xadd(
            f"{self._prefix}events:{stream}",
            payload,
            maxlen=2_000,
            approximate=True,
        )

    def xread(
        self,
        stream: str,
        *,
        last_id: str = "$",
        block_ms: int = 15_000,
        count: int = 100,
    ) -> list[dict[str, Any]]:
        if last_id != "$" and not re.fullmatch(r"\d+-\d+", last_id):
            last_id = "$"
        rows = self._blocking_client.xread(
            {f"{self._prefix}events:{stream}": last_id},
            count=min(max(count, 1), 500),
            block=min(max(block_ms, 1_000), 15_000),
        )
        events: list[dict[str, Any]] = []
        for _, entries in rows:
            for entry_id, fields in entries:
                decoded: dict[str, Any] = {}
                for key, value in fields.items():
                    try:
                        decoded[key] = json.loads(value)
                    except (TypeError, json.JSONDecodeError):
                        decoded[key] = value
                events.append({"id": entry_id, **decoded})
        return events

    def xread_recent(self, stream: str, count: int = 50) -> list[dict[str, Any]]:
        entries = self._client.xrevrange(f"{self._prefix}events:{stream}", count=count)
        events: list[dict[str, Any]] = []
        for entry_id, data in reversed(entries):
            events.append({"id": entry_id, **data})
        return events

    def get_llm_cache(self, cache_key: str) -> str | None:
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        return self._client.get(self._key("cache:llm", digest))

    def set_llm_cache(self, cache_key: str, value: str, *, ttl_seconds: int = 86_400) -> None:
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        self._client.set(self._key("cache:llm", digest), value, ex=ttl_seconds)

    def flush_loopie_keys(self) -> None:
        batch: list[str] = []
        for key in self._client.scan_iter(f"{self._prefix}*", count=200):
            batch.append(key)
            if len(batch) == 200:
                with self._client.pipeline(transaction=False) as pipe:
                    pipe.delete(*batch)
                    pipe.execute()
                batch.clear()
        if batch:
            self._client.delete(*batch)

    def close(self) -> None:
        self._blocking_client.close()
        self._client.close()

    def preflight_capabilities(self) -> dict[str, Any]:
        """Probe Redis module/capability presence (not memory sizing)."""
        caps: dict[str, Any] = {
            "ping": False,
            "json": False,
            "search": False,
            "timeseries": False,
            "vector": False,
            "cluster_mode": False,
            "db": 0,
        }
        try:
            caps["ping"] = bool(self.ping())
            info = self._client.info("server")
            caps["cluster_mode"] = str(info.get("redis_mode", "")).lower() == "cluster"
        except Exception:
            return caps

        try:
            self._client.execute_command("JSON.SET", f"{self._prefix}preflight:json", "$", '{"ok":true}')
            self._client.delete(f"{self._prefix}preflight:json")
            caps["json"] = True
        except Exception:
            caps["json"] = False

        for module, flag in (("search", "search"), ("timeseries", "timeseries"), ("vector", "vector")):
            try:
                self._client.execute_command("MODULE", "LIST")
                modules = self._client.execute_command("MODULE", "LIST") or []
                text = str(modules).lower()
                caps[flag] = "search" in text or "ft" in text if flag == "search" else flag in text
            except Exception:
                caps[flag] = False

        return caps

    def set_artifact_doc(self, artifact_key: str, doc: dict[str, Any]) -> bool:
        """RedisJSON partial-friendly artifact doc when module is present."""
        key = f"{self._prefix}artifact:doc:{artifact_key}"
        try:
            self._client.execute_command("JSON.SET", key, "$", json.dumps(doc))
            return True
        except Exception:
            self._client.set(key, json.dumps(doc))
            return False

    def patch_artifact_doc(self, artifact_key: str, path: str, value: Any) -> bool:
        key = f"{self._prefix}artifact:doc:{artifact_key}"
        try:
            self._client.execute_command("JSON.SET", key, path, json.dumps(value))
            return True
        except Exception:
            raw = self._client.get(key)
            doc = json.loads(raw) if raw else {}
            if path.startswith("$."):
                doc[path[2:]] = value
            self._client.set(key, json.dumps(doc))
            return False

    def get_artifact_doc(self, artifact_key: str) -> dict[str, Any] | None:
        key = f"{self._prefix}artifact:doc:{artifact_key}"
        try:
            raw = self._client.execute_command("JSON.GET", key)
            return json.loads(raw) if raw else None
        except Exception:
            raw = self._client.get(key)
            return json.loads(raw) if raw else None
