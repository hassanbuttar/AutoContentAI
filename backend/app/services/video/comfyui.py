"""ComfyUI integration: connectivity, workflow validation against /object_info, submission,
WebSocket progress (with /history polling fallback), timeouts/cancellation, output retrieval."""
from __future__ import annotations

import copy
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)
MODEL_INPUT_HINTS = ("ckpt_name", "unet_name", "vae_name", "clip_name", "clip_name1", "clip_name2", "lora_name",
                     "model_name", "control_net_name", "upscale_model")
MAPPING_KEYS = ("positive_prompt", "negative_prompt", "seed", "width", "height", "length", "fps", "steps",
                "input_image", "filename_prefix", "batch_size")


class ComfyError(RuntimeError):
    def __init__(self, message: str, actionable: str = ""):
        super().__init__(message)
        self.actionable = actionable


@dataclass
class ComfyOutput:
    files: list[Path] = field(default_factory=list)
    prompt_id: str = ""
    seconds: float = 0.0


def is_ui_format(wf: dict) -> bool:
    return isinstance(wf, dict) and "nodes" in wf and "links" in wf


class ComfyClient:
    def __init__(self, base_url: str, timeout: float = 30.0, transport: httpx.BaseTransport | None = None):
        self.base = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base, timeout=timeout, transport=transport)
        self.client_id = uuid.uuid4().hex
        self._object_info: dict | None = None

    # -------------------------------------------------------------- status
    def system_stats(self) -> dict:
        try:
            r = self.http.get("/system_stats")
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as exc:
            raise ComfyError(f"ComfyUI not reachable at {self.base}: {type(exc).__name__}",
                             "Start ComfyUI (default port 8188) or fix the URL in Settings → ComfyUI.") from exc

    def queue_state(self) -> dict:
        r = self.http.get("/queue")
        r.raise_for_status()
        q = r.json()
        return {"running": len(q.get("queue_running", [])), "pending": len(q.get("queue_pending", []))}

    def object_info(self, refresh: bool = False) -> dict:
        if self._object_info is None or refresh:
            r = self.http.get("/object_info", timeout=120)
            r.raise_for_status()
            self._object_info = r.json()
        return self._object_info

    # -------------------------------------------------------------- validation
    def validate(self, workflow: dict, mappings: dict) -> dict:
        """Check node classes, mapped inputs and model files against the live server."""
        problems: list[str] = []
        warnings: list[str] = []
        if is_ui_format(workflow):
            return {"ok": False, "problems": ["This is a UI-format workflow. In ComfyUI use Workflow → Export (API) and import that file."],
                    "warnings": [], "missing_models": [], "missing_nodes": []}
        info = self.object_info()
        missing_nodes, missing_models = [], []
        for nid, node in workflow.items():
            if not isinstance(node, dict) or "class_type" not in node:
                problems.append(f"Node {nid} is not a valid API-format node")
                continue
            cls = node["class_type"]
            spec = info.get(cls)
            if spec is None:
                missing_nodes.append(cls)
                continue
            req = {**spec.get("input", {}).get("required", {}), **spec.get("input", {}).get("optional", {})}
            for name, val in (node.get("inputs") or {}).items():
                if isinstance(val, list):  # link to another node
                    continue
                opt = req.get(name)
                if opt and isinstance(opt[0], list) and name in MODEL_INPUT_HINTS and val not in opt[0]:
                    missing_models.append(f"{cls}.{name} = {val}")
        for key, m in (mappings or {}).items():
            if key not in MAPPING_KEYS:
                warnings.append(f"Unknown mapping key '{key}' (ignored)")
                continue
            node = workflow.get(str(m.get("node")))
            if node is None:
                problems.append(f"Mapping '{key}' points to missing node {m.get('node')}")
            elif m.get("input") not in (node.get("inputs") or {}):
                problems.append(f"Mapping '{key}': node {m.get('node')} ({node.get('class_type')}) has no input '{m.get('input')}'")
        if "positive_prompt" not in (mappings or {}) and "input_image" not in (mappings or {}):
            problems.append("Map at least 'positive_prompt' (txt2img/txt2vid) or 'input_image' (img2vid)")
        if missing_nodes:
            problems.append("Missing custom nodes: " + ", ".join(sorted(set(missing_nodes))))
        if missing_models:
            problems.append("Model files not installed: " + "; ".join(missing_models))
        return {"ok": not problems, "problems": problems, "warnings": warnings,
                "missing_nodes": sorted(set(missing_nodes)), "missing_models": missing_models}

    # -------------------------------------------------------------- execution
    def upload_image(self, path: Path) -> str:
        with open(path, "rb") as fh:
            r = self.http.post("/upload/image", files={"image": (Path(path).name, fh, "image/png")},
                               data={"overwrite": "true", "type": "input"})
        r.raise_for_status()
        d = r.json()
        return f"{d['subfolder']}/{d['name']}" if d.get("subfolder") else d["name"]

    @staticmethod
    def apply_mappings(workflow: dict, mappings: dict, values: dict[str, Any]) -> dict:
        wf = copy.deepcopy(workflow)
        for key, val in values.items():
            m = (mappings or {}).get(key)
            if not m or val is None:
                continue
            node = wf.get(str(m["node"]))
            if node is not None:
                node.setdefault("inputs", {})[m["input"]] = val
        return wf

    def submit(self, workflow: dict) -> str:
        r = self.http.post("/prompt", json={"prompt": workflow, "client_id": self.client_id})
        if r.status_code >= 400:
            try:
                err = r.json()
                detail = err.get("error", {}).get("message", "") + " " + json.dumps(err.get("node_errors", {}))[:600]
            except Exception:
                detail = r.text[:600]
            raise ComfyError(f"ComfyUI rejected the workflow: {detail}", "Validate the workflow in the ComfyUI Workflows page.")
        return r.json()["prompt_id"]

    def history(self, prompt_id: str) -> dict | None:
        r = self.http.get(f"/history/{prompt_id}")
        r.raise_for_status()
        return r.json().get(prompt_id)

    def cancel(self, prompt_id: str) -> None:
        try:
            self.http.post("/queue", json={"delete": [prompt_id]})
            q = self.http.get("/queue").json()
            if any(item[1] == prompt_id for item in q.get("queue_running", [])):
                self.http.post("/interrupt")
        except Exception:
            log.warning("ComfyUI cancel failed for %s", prompt_id)

    def wait(self, prompt_id: str, timeout: float, on_progress: Callable[[float, str], None] | None = None,
             should_cancel: Callable[[], bool] | None = None) -> dict:
        """Wait for completion. Uses the WebSocket for live progress, /history as source of truth."""
        deadline = time.monotonic() + timeout
        ws = None
        try:
            import websockets.sync.client as wsc

            u = urlparse(self.base)
            scheme = "wss" if u.scheme == "https" else "ws"
            ws = wsc.connect(f"{scheme}://{u.netloc}{u.path}/ws?clientId={self.client_id}", open_timeout=5, close_timeout=2)
        except Exception:
            ws = None
        try:
            last_poll = 0.0
            while time.monotonic() < deadline:
                if should_cancel and should_cancel():
                    self.cancel(prompt_id)
                    raise ComfyError("Cancelled by user")
                if ws is not None:
                    try:
                        msg = ws.recv(timeout=1.0)
                        if isinstance(msg, str):
                            data = json.loads(msg)
                            d = data.get("data", {})
                            if d.get("prompt_id") not in (None, prompt_id):
                                continue
                            if data.get("type") == "progress" and on_progress and d.get("max"):
                                on_progress(d["value"] / d["max"], f"ComfyUI step {d['value']}/{d['max']}")
                            elif data.get("type") == "execution_error":
                                raise ComfyError(f"ComfyUI execution error in node {d.get('node_type')}: {d.get('exception_message', '')[:400]}",
                                                 "Open the workflow in ComfyUI to debug; check VRAM and model files.")
                    except TimeoutError:
                        pass
                    except ComfyError:
                        raise
                    except Exception:
                        ws = None  # socket dropped; continue with polling
                else:
                    time.sleep(1.0)
                if time.monotonic() - last_poll > 2.0:
                    last_poll = time.monotonic()
                    h = self.history(prompt_id)
                    if h:
                        st = h.get("status", {})
                        if st.get("status_str") == "error":
                            msgs = [m for m in st.get("messages", []) if m and m[0] == "execution_error"]
                            detail = msgs[0][1].get("exception_message", "")[:400] if msgs else "unknown error"
                            raise ComfyError(f"ComfyUI execution failed: {detail}", "Check the ComfyUI console for details.")
                        if st.get("completed") or h.get("outputs"):
                            return h
            self.cancel(prompt_id)
            raise ComfyError(f"ComfyUI job timed out after {int(timeout)}s",
                             "Increase the workflow timeout, reduce steps/resolution, or check the GPU is free.")
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

    def download_outputs(self, hist: dict, out_dir: Path, prefix: str) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        files: list[Path] = []
        items: list[tuple[str, dict]] = []
        for node_id, out in (hist.get("outputs") or {}).items():
            for kind in ("videos", "gifs", "images"):
                for item in out.get(kind, []) or []:
                    if isinstance(item, dict) and item.get("filename"):
                        items.append((node_id, item))
        saved = [x for x in items if x[1].get("type", "output") == "output"]
        for node_id, item in (saved or items):  # prefer saved outputs over temp previews
            fn = item["filename"]
            r = self.http.get("/view", params={"filename": fn, "subfolder": item.get("subfolder", ""),
                                               "type": item.get("type", "output")}, timeout=300)
            r.raise_for_status()
            suffix = Path(fn).suffix.lower() or ".png"
            dest = out_dir / f"{prefix}_{node_id}_{len(files)}{suffix}"
            dest.write_bytes(r.content)
            files.append(dest)
        if not files:
            raise ComfyError("Workflow finished but produced no downloadable output",
                             "Make sure the workflow ends in SaveImage / SaveVideo / VHS_VideoCombine.")
        return files

    def run(self, workflow: dict, mappings: dict, values: dict, out_dir: Path, prefix: str, timeout: float,
            on_progress=None, should_cancel=None) -> ComfyOutput:
        t0 = time.monotonic()
        if "seed" in mappings and values.get("seed") is None:
            values["seed"] = random.randint(1, 2**31 - 1)
        wf = self.apply_mappings(workflow, mappings, values)
        pid = self.submit(wf)
        hist = self.wait(pid, timeout, on_progress, should_cancel)
        files = self.download_outputs(hist, out_dir, prefix)
        return ComfyOutput(files=files, prompt_id=pid, seconds=time.monotonic() - t0)


def summarize_stats(stats: dict) -> dict:
    devs = stats.get("devices") or []
    gpu = next((d for d in devs if d.get("type") in ("cuda", "rocm", "mps", "xpu")), devs[0] if devs else {})
    return {"version": stats.get("system", {}).get("comfyui_version"), "os": stats.get("system", {}).get("os"),
            "gpu": gpu.get("name"), "vram_total_gb": round((gpu.get("vram_total") or 0) / 1e9, 1),
            "vram_free_gb": round((gpu.get("vram_free") or 0) / 1e9, 1)}
