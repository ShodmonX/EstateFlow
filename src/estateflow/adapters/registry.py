from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

from estateflow.adapters.contracts import (
    AdapterLoadFailure,
    AdapterLoadResult,
    AdapterManifest,
    AdapterReloadReport,
)
from estateflow.application.core.config import Settings


class AdapterPlugin(Protocol):
    manifest: AdapterManifest


@dataclass(slots=True)
class LoadedAdapterPlugin:
    plugin_name: str
    module_path: Path
    module_name: str
    manifest: AdapterManifest
    plugin: AdapterPlugin
    file_hash: str


class AdapterRegistry:
    def __init__(self, plugin_directory: Path, settings: Settings) -> None:
        self._plugin_directory = plugin_directory
        self._settings = settings
        self._plugins: dict[str, LoadedAdapterPlugin] = {}
        self._failures: dict[str, AdapterLoadFailure] = {}
        self._lock = asyncio.Lock()

    @property
    def plugin_directory(self) -> Path:
        return self._plugin_directory

    def list_plugins(self) -> list[LoadedAdapterPlugin]:
        return sorted(self._plugins.values(), key=lambda item: item.plugin_name)

    def list_failures(self) -> list[AdapterLoadFailure]:
        return sorted(self._failures.values(), key=lambda item: item.plugin_name)

    def get(self, plugin_name: str) -> LoadedAdapterPlugin | None:
        return self._plugins.get(plugin_name)

    async def reload(self) -> AdapterReloadReport:
        async with self._lock:
            return self._reload_locked()

    def reload_sync(self) -> AdapterReloadReport:
        if self._lock.locked():
            raise RuntimeError("Adapter registry is already reloading.")
        return self._reload_locked()

    def _reload_locked(self) -> AdapterReloadReport:
        if not self._plugin_directory.exists():
            self._plugin_directory.mkdir(parents=True, exist_ok=True)

        loaded: dict[str, LoadedAdapterPlugin] = {}
        failures: dict[str, AdapterLoadFailure] = {}
        results: list[AdapterLoadResult] = []

        for path in sorted(self._plugin_directory.glob("*.py")):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            plugin_name = path.stem
            try:
                loaded_plugin = self._load_plugin(path, plugin_name)
                loaded[plugin_name] = loaded_plugin
                results.append(
                    AdapterLoadResult(
                        plugin_name=plugin_name,
                        module_path=str(path),
                        state="loaded",
                        manifest=loaded_plugin.manifest,
                        loaded_at=datetime.now(UTC),
                    )
                )
            except Exception as exc:
                failures[plugin_name] = AdapterLoadFailure(
                    plugin_name=plugin_name,
                    module_path=str(path),
                    error=str(exc),
                )
                existing = self._plugins.get(plugin_name)
                if existing is not None:
                    loaded[plugin_name] = existing
                    results.append(
                        AdapterLoadResult(
                            plugin_name=plugin_name,
                            module_path=str(path),
                            state="loaded",
                            manifest=existing.manifest,
                            error=str(exc),
                            loaded_at=datetime.now(UTC),
                        )
                    )
                else:
                    results.append(
                        AdapterLoadResult(
                            plugin_name=plugin_name,
                            module_path=str(path),
                            state="failed",
                            error=str(exc),
                        )
                    )

        self._plugins = loaded
        self._failures = failures
        return AdapterReloadReport(
            plugin_directory=str(self._plugin_directory),
            loaded_count=sum(1 for item in results if item.state == "loaded"),
            failed_count=sum(1 for item in results if item.state == "failed"),
            loaded_plugins=results,
            failures=list(failures.values()),
        )

    def _load_plugin(self, path: Path, plugin_name: str) -> LoadedAdapterPlugin:
        module_name = f"estateflow.runtime_plugins.{plugin_name}"
        sys.modules.pop(module_name, None)
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load plugin spec for {path}.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        plugin = self._instantiate_plugin(module, path, plugin_name)
        manifest = AdapterManifest.model_validate(plugin.manifest)
        if manifest.name != plugin_name:
            raise ValueError(
                f"Plugin manifest name {manifest.name!r} must match file stem {plugin_name!r}."
            )
        return LoadedAdapterPlugin(
            plugin_name=plugin_name,
            module_path=path,
            module_name=module_name,
            manifest=manifest,
            plugin=plugin,
            file_hash=_sha256(path),
        )

    def _instantiate_plugin(
        self,
        module: ModuleType,
        path: Path,
        plugin_name: str,
    ) -> AdapterPlugin:
        factory = getattr(module, "create_plugin", None)
        if factory is None or not callable(factory):
            raise AttributeError(
                f"Plugin module {path.name} must expose a callable create_plugin(settings)."
            )
        plugin = factory(self._settings)
        if not hasattr(plugin, "manifest"):
            raise AttributeError(f"Plugin {plugin_name!r} must expose a manifest attribute.")
        return cast(AdapterPlugin, plugin)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()
