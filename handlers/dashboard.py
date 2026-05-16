"""Dashboard UI endpoint handlers.

Mixin class for GlowUpRequestHandler.  Extracted from server.py.
"""

# Copyright (c) 2026 Perry Kivolowitz. All rights reserved.
# Licensed under the MIT License. See LICENSE file in the project root.

__version__: str = "1.0"

import json
import logging

logger: logging.Logger = logging.getLogger("glowup.dashboard")
import math
import os
import socket
import struct
import threading
import time as time_mod
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from urllib.parse import unquote

# server_constants not used in this module.
from atomic_io import write_json_atomic
from operators import OperatorManager
from media import SignalBus
from schedule_utils import parse_time_spec as _parse_time_spec
from solar import sun_times

# Max seconds without a non-time signal on glowup/signals/# before
# broker-2 is reported unhealthy.  Out-of-process producers publish
# cross-host to the hub using this topic prefix.  The hub's
# _on_remote_signal callback stamps a class-level timestamp on every
# non-time message.  120s covers the slowest expected publisher
# cadence — any one producer being alive keeps the timestamp fresh.
BROKER2_SIGNALS_STALE_SEC: float = 120.0


class DashboardHandlerMixin:
    """Dashboard UI endpoint handlers."""

    def _handle_get_root(self) -> None:
        """GET / — 302 redirect to /dashboard.

        The /dashboard page is the LIFX install's primary surface.
        A bare ``http://<host>:8420/`` previously returned 404, which
        reads as "this didn't install correctly" even when the server
        is healthy.  302 (rather than 301) so the browser revisits /
        on each load — keeps room for the redirect target to change
        without leaving permanent caches behind.
        """
        self.send_response(302)
        self.send_header("Location", "/dashboard")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle_get_dashboard(self) -> None:
        """GET /dashboard — serve the static HTML dashboard page.

        Reads ``static/dashboard.html`` from the server's directory
        and returns it as ``text/html``.  Returns 404 if the file
        is missing.
        """
        dashboard_path: str = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "dashboard.html",
        )
        try:
            with open(dashboard_path, "r") as f:
                html: str = f.read()
            body: bytes = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # Prevent browser caching so dashboard updates deploy instantly.
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self._send_json(404, {"error": "Dashboard page not found"})


    def _handle_get_operators(self) -> None:
        """GET /api/operators — list running operators with status.

        Response::

            {"operators": [{name, type, started, tick_mode, ...}, ...]}
        """
        om: Optional[OperatorManager] = self.operator_manager
        if om is not None:
            self._send_json(200, {"operators": om.get_status()})
        else:
            self._send_json(200, {"operators": []})


    # --- Binding CRUD endpoints -------------------------------------------

    def _handle_get_bindings(self) -> None:
        """GET /api/signals/bindings — list all active param bindings.

        Response::

            {"bindings": [
                {"operator": "occ", "param": "away_confirm_seconds",
                 "target": "occ:away_confirm_seconds",
                 "source": "house:occupancy:state",
                 "scale": [5.0, 1.0]},
                ...
            ]}
        """
        om: Optional[OperatorManager] = self.operator_manager
        if om is not None:
            self._send_json(200, {"bindings": om.get_all_bindings()})
        else:
            self._send_json(200, {"bindings": []})

    def _handle_post_binding(self) -> None:
        """POST /api/signals/bindings — create or replace a binding.

        Request body::

            {"operator": "cylon_runner", "param": "speed",
             "signal": "breathe_runner:speed",
             "scale": [0.1, 30.0], "reduce": "max"}

        Responds 400 if the binding would create a cycle, the operator
        is not found, or the param does not exist.
        """
        om: Optional[OperatorManager] = self.operator_manager
        if om is None:
            self._send_json(503, {"error": "Operator manager not running"})
            return
        body: dict = self._read_json_body()
        if not body:
            self._send_json(400, {"error": "Missing request body"})
            return
        op_name: str = body.get("operator", "")
        param_name: str = body.get("param", "")
        source: str = body.get("signal", "")
        if not op_name or not param_name or not source:
            self._send_json(400, {
                "error": "Required fields: operator, param, signal",
            })
            return
        spec: dict = {"signal": source}
        if "scale" in body:
            spec["scale"] = body["scale"]
        if "reduce" in body:
            spec["reduce"] = body["reduce"]
        try:
            om.create_binding(op_name, param_name, spec)
            self._send_json(200, {"ok": True, "binding": {
                "target": f"{op_name}:{param_name}",
                "source": source,
            }})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})

    def _handle_delete_binding(self, target: str) -> None:
        """DELETE /api/signals/bindings/{target} — remove a binding.

        The *target* path segment is ``operator:param`` (e.g.,
        ``cylon_runner:speed``).  Param keeps its last bound value.
        """
        om: Optional[OperatorManager] = self.operator_manager
        if om is None:
            self._send_json(503, {"error": "Operator manager not running"})
            return
        parts: list[str] = target.split(":", 1)
        if len(parts) != 2:
            self._send_json(400, {
                "error": "Target must be operator:param (e.g., occ:speed)",
            })
            return
        op_name, param_name = parts
        try:
            om.remove_binding(op_name, param_name)
            self._send_json(200, {"ok": True})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})

    def _handle_get_nav_config(self) -> None:
        """GET /api/config/nav — navigation links for the site nav bar.

        Returns the list of nav links from server.json ``nav_links``.
        Pages build the nav bar dynamically from this endpoint so
        no internal IPs are hardcoded in HTML.

        Default links (Dashboard, I/O) are always included.  External
        links come from config.
        """
        # Built-in pages — always present.
        links: list[dict[str, str]] = [
            {"label": "Dashboard", "href": "/dashboard"},
            {"label": "I/O", "href": "/io"},
        ]
        # External links from config.
        extra: list[dict[str, str]] = self.config.get("nav_links", [])
        links.extend(extra)
        self._send_json(200, {"links": links})

    def _handle_get_io_page(self) -> None:
        """GET /io — serve the I/O timing dashboard."""
        static_dir: str = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static",
        )
        path: str = os.path.join(static_dir, "io.html")
        try:
            with open(path, "rb") as f:
                content: bytes = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self._send_json(404, {"error": "io.html not found"})

    def _handle_get_io_stats(self) -> None:
        """GET /api/io/stats — timed I/O histogram data per label.

        Returns per-label statistics: call count, timeout count,
        min/max/avg/p50/p95/p99 in milliseconds, and the assigned
        IO class.  Used by the IO dashboard to visualize blocking
        operation performance.

        Response::

            {
              "labels": {
                "lanscan.arp": {
                  "class": "FAST",
                  "count": 342,
                  "timeouts": 2,
                  "min_ms": 0.1,
                  "max_ms": 1800.0,
                  "avg_ms": 12.3,
                  "p50_ms": 8.1,
                  "p95_ms": 45.2,
                  "p99_ms": 180.0
                }
              }
            }
        """
        from infrastructure.timed_io import get_all_stats, WINDOW_SECONDS
        all_stats = get_all_stats()
        result: dict[str, dict[str, Any]] = {}
        for label, stats in all_stats.items():
            result[label] = {
                "class": stats.io_class.name,
                "window": {
                    "seconds": WINDOW_SECONDS,
                    "count": stats.window_count(),
                    "exceeded": stats.window_exceeded(),
                    "min_ms": round(stats.window_min_ms(), 1),
                    "max_ms": round(stats.window_max_ms(), 1),
                    "avg_ms": round(stats.window_avg_ms(), 1),
                    "stddev_ms": round(stats.window_stddev_ms(), 1),
                    "p50_ms": round(stats.window_percentile(0.50), 1),
                    "p95_ms": round(stats.window_percentile(0.95), 1),
                    "p99_ms": round(stats.window_percentile(0.99), 1),
                },
                "lifetime": {
                    "count": stats.count,
                    "exceeded": stats.timeout_count,
                    "min_ms": round(stats.min_ms, 1)
                        if stats.min_ms != float("inf") else 0.0,
                    "max_ms": round(stats.max_ms, 1),
                    "avg_ms": round(stats.avg_ms(), 1),
                    "stddev_ms": round(stats.stddev_ms(), 1),
                },
            }
        self._send_json(200, {"labels": result})

    def _handle_get_static_js(self, filename: str) -> None:
        """GET /js/{filename} — serve a shared JavaScript file from static/js/.

        All dashboards share reusable client-side code (site nav bar,
        future shared widgets).  Mirrors ``_handle_get_photo`` for path
        validation and MIME handling.  Only ``.js`` files are served.
        Directory traversal is rejected.
        """
        # Reject any path traversal attempts.
        if "/" in filename or "\\" in filename or ".." in filename:
            self._send_json(400, {"error": "Invalid filename"})
            return
        # Only serve .js — this handler is not a general static server.
        if not filename.endswith(".js"):
            self._send_json(400, {"error": "Only .js files are served"})
            return
        js_path: str = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", filename,
        )
        try:
            with open(js_path, "rb") as f:
                data: bytes = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            # 5-minute cache — short enough for fast iteration,
            # long enough to matter on multi-tab clients.
            self.send_header("Cache-Control", "public, max-age=300")
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self._send_json(404, {"error": f"JS file not found: {filename}"})


    def _handle_get_photo(self, filename: str) -> None:
        """GET /photos/{filename} — serve a photo from static/photos/.

        Validates the filename to prevent directory traversal,
        then serves the image with appropriate content type.
        """
        # Content types by extension.
        CONTENT_TYPES: dict[str, str] = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".gif": "image/gif",
            ".webp": "image/webp",
        }
        # Reject any path traversal attempts.
        if "/" in filename or "\\" in filename or ".." in filename:
            self._send_json(400, {"error": "Invalid filename"})
            return
        _, ext = os.path.splitext(filename)
        ctype: str = CONTENT_TYPES.get(ext.lower(), "")
        if not ctype:
            self._send_json(400, {"error": "Unsupported image type"})
            return
        photo_path: str = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "photos", filename,
        )
        try:
            with open(photo_path, "rb") as f:
                data: bytes = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            # Cache photos for 5 minutes — they change rarely.
            self.send_header("Cache-Control", "public, max-age=300")
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self._send_json(404, {"error": f"Photo not found: {filename}"})


    def _save_config_field(self, key: str, value: Any) -> None:
        """Persist a single config field to the config file.

        Reads the config JSON, updates the given key, and writes back.
        Schedule entries are saved to the external schedule file if
        one is configured (``_schedule_path``).

        Serialized by ``_config_save_lock`` so concurrent saves on
        different keys do not clobber each other.

        Args:
            key:   Top-level config key to update.
            value: The new value.
        """
        with self._config_save_lock:
            # All three write paths below use ``write_json_atomic`` so
            # that a SIGKILL or power loss during the write never
            # leaves the state file truncated / unparseable; the worst
            # case is the previous good contents survive.  See
            # atomic_io.py for the durability boundary this provides.

            # Route schedule writes to the schedule file if it exists.
            sched_path: Optional[str] = self.config.get("_schedule_path")
            if key == "schedule" and sched_path:
                try:
                    with open(sched_path, "r") as f:
                        sched_config: dict[str, Any] = json.load(f)
                    sched_config["schedule"] = value
                    write_json_atomic(sched_path, sched_config)
                except Exception as exc:
                    logging.exception(
                        "Failed to save schedule to '%s'",
                        sched_path,
                    )
                return

            # Route groups writes to the groups file if it exists.
            # Mirrors the schedule_file pattern above — when the
            # operator's server.json sets ``groups_file``, server.py's
            # ``_load_config`` stamps ``_groups_path`` into the live
            # config dict; we look for it here and write the registry
            # directly to that file rather than back into server.json.
            # The file's top-level shape is the groups dict itself
            # (``{name: [entries], ...}``), not wrapped in another
            # key, matching server.py's ``groups_data`` consumer.
            groups_path: Optional[str] = self.config.get("_groups_path")
            if key == "groups" and groups_path:
                try:
                    write_json_atomic(groups_path, value)
                except Exception as exc:
                    logging.exception(
                        "Failed to save groups to '%s'",
                        groups_path,
                    )
                return

            config_path: Optional[str] = self.config_path
            if config_path is None:
                return
            try:
                with open(config_path, "r") as f:
                    config: dict[str, Any] = json.load(f)
                config[key] = value
                write_json_atomic(config_path, config)
            except Exception as exc:
                logging.warning(
                    "Failed to save config field '%s': %s",
                    key, exc, exc_info=True,
                )

    # -- Helpers ------------------------------------------------------------


