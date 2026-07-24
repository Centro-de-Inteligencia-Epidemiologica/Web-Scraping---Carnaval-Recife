"""First-run dependency provisioning.

On a fresh machine the packaged app needs three things that are NOT bundled
into the EXE (they are large and/or machine-specific):

  1. The Playwright Chromium browser  -> installed into the per-user cache.
  2. Ollama itself                     -> official Windows installer.
  3. The LLM models (text + vision)    -> pulled via the Ollama API.

This module only contains detection + install logic with progress callbacks;
the UI lives in app/ui/bootstrap_dialog.py. Everything here is import-safe and
works both from source and inside a PyInstaller onedir bundle.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional

import requests

OLLAMA_INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"

# Hide the console window spawned by helper subprocesses on Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)

LogFn = Callable[[str], None]


def _noop(_: str) -> None:
    pass


# ----------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------

def _default_browsers_path() -> Optional[Path]:
    """Playwright's own per-OS default browser cache directory."""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        return Path(local) / "ms-playwright" if local else None
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "ms-playwright"


def chromium_installed() -> bool:
    """True if a Playwright Chromium build exists in the browser cache."""
    candidates: List[Path] = []
    env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_path and env_path != "0":
        candidates.append(Path(env_path))
    default = _default_browsers_path()
    if default:
        candidates.append(default)
    for base in candidates:
        try:
            if base.exists() and (
                any(base.glob("chromium-*")) or any(base.glob("chromium_headless_shell-*"))
            ):
                return True
        except Exception:
            continue
    return False


def ollama_exe_path() -> Optional[str]:
    """Locate ollama.exe, or None if Ollama is not installed."""
    local = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("ProgramFiles", "")
    for p in (
        Path(local) / "Programs" / "Ollama" / "ollama.exe",
        Path(local) / "Ollama" / "ollama.exe",
        Path(pf) / "Ollama" / "ollama.exe",
    ):
        if p.exists():
            return str(p)
    found = shutil.which("ollama")
    return found


def ollama_server_up(endpoint: str) -> bool:
    try:
        requests.get(f"{endpoint.rstrip('/')}/api/tags", timeout=3)
        return True
    except Exception:
        return False


def _norm(name: str) -> str:
    return name if ":" in name else f"{name}:latest"


def installed_models(endpoint: str) -> set:
    try:
        r = requests.get(f"{endpoint.rstrip('/')}/api/tags", timeout=5)
        r.raise_for_status()
        return {m["name"] for m in r.json().get("models", [])}
    except Exception:
        return set()


def missing_models(endpoint: str, names: List[str]) -> List[str]:
    have = installed_models(endpoint)
    have_norm = {_norm(h) for h in have}
    return [n for n in names if _norm(n) not in have_norm]


# ----------------------------------------------------------------------
# Plan
# ----------------------------------------------------------------------

def compute_plan(endpoint: str, model_names: List[str]) -> dict:
    """Return what needs doing. Empty steps => nothing to provision."""
    has_chromium = chromium_installed()
    exe = ollama_exe_path()
    server = ollama_server_up(endpoint) if exe or True else False
    models_missing = missing_models(endpoint, model_names) if server else list(model_names)

    return {
        "need_chromium": not has_chromium,
        "need_ollama": exe is None,
        "ollama_exe": exe,
        "need_server": (exe is not None) and (not server),
        "missing_models": models_missing,
    }


def plan_has_work(plan: dict) -> bool:
    return (
        plan["need_chromium"]
        or plan["need_ollama"]
        or plan["need_server"]
        or bool(plan["missing_models"])
    )


# ----------------------------------------------------------------------
# Playwright Chromium
# ----------------------------------------------------------------------

def _driver_cmd():
    """Command prefix that runs the Playwright CLI (frozen-safe)."""
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
        env = get_driver_env()
        exe = compute_driver_executable()
        cmd = list(exe) if isinstance(exe, (list, tuple)) else [exe]
        return cmd, {**os.environ, **env}
    except Exception:
        import playwright
        base = Path(playwright.__file__).parent / "driver"
        node = base / ("node.exe" if sys.platform == "win32" else "node")
        cli = base / "package" / "cli.js"
        return [str(node), str(cli)], dict(os.environ)


def install_chromium(log: LogFn = _noop) -> bool:
    cmd, env = _driver_cmd()
    log("Baixando o navegador Chromium (~150 MB)… isso pode demorar.")
    try:
        proc = subprocess.Popen(
            cmd + ["install", "chromium"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=_NO_WINDOW,
        )
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if line:
                log("  " + line)
        proc.wait()
        ok = proc.returncode == 0 and chromium_installed()
        log("✔ Chromium instalado." if ok else "✘ Falha ao instalar o Chromium.")
        return ok
    except Exception as exc:
        log(f"✘ Erro instalando Chromium: {exc}")
        return False


# ----------------------------------------------------------------------
# Ollama installer
# ----------------------------------------------------------------------

def install_ollama(log: LogFn = _noop, wait_seconds: int = 600) -> Optional[str]:
    """Download and launch the official Ollama installer, then wait for it.

    Returns the ollama.exe path once detected, or None on timeout/failure.
    The installer shows its own UI (and a UAC prompt) — the user completes it.

    Windows only: the official installer is a .exe. On Linux/macOS there's no
    unattended equivalent we can run from here (the official script needs
    sudo), so we just point the user at it instead of silently failing after
    downloading ~700 MB they can't use.
    """
    if sys.platform != "win32":
        log("✘ Ollama não encontrado.")
        if sys.platform == "darwin":
            log("  Instale manualmente: baixe em https://ollama.com/download")
        else:
            log("  Instale manualmente executando no terminal:")
            log("    curl -fsSL https://ollama.com/install.sh | sh")
            log("  (requer sua senha de administrador) e depois reabra o InstaEpi Monitor.")
        return None

    dest = Path(tempfile.gettempdir()) / "OllamaSetup.exe"
    log("Baixando o instalador do Ollama (~700 MB)…")
    try:
        last = [-1]

        def _hook(blocks, block_size, total):
            if total > 0:
                pct = min(100, int(blocks * block_size * 100 / total))
                if pct != last[0] and pct % 5 == 0:
                    last[0] = pct
                    log(f"  download: {pct}%")

        urllib.request.urlretrieve(OLLAMA_INSTALLER_URL, dest, reporthook=_hook)
    except Exception as exc:
        log(f"✘ Falha ao baixar o instalador: {exc}")
        return None

    log("Abrindo o instalador do Ollama — conclua a instalação na janela (UAC).")
    try:
        subprocess.Popen([str(dest)])
    except Exception as exc:
        log(f"✘ Não foi possível abrir o instalador: {exc}")
        return None

    log(f"Aguardando a conclusão da instalação (até {wait_seconds}s)…")
    waited = 0
    while waited < wait_seconds:
        exe = ollama_exe_path()
        if exe:
            log("✔ Ollama instalado.")
            return exe
        time.sleep(3)
        waited += 3
    log("✘ Tempo esgotado aguardando a instalação do Ollama.")
    return None


def ensure_server(endpoint: str, exe: Optional[str], log: LogFn = _noop) -> bool:
    if ollama_server_up(endpoint):
        return True
    if not exe:
        return False
    log("Iniciando o servidor Ollama…")
    try:
        subprocess.Popen(
            [exe, "serve"],
            creationflags=_NO_WINDOW | _DETACHED,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        log(f"  aviso: {exc}")
    for _ in range(30):
        if ollama_server_up(endpoint):
            log("✔ Servidor Ollama no ar.")
            return True
        time.sleep(1)
    return ollama_server_up(endpoint)


def pull_model(endpoint: str, name: str, log: LogFn = _noop) -> bool:
    log(f"Baixando modelo {name}…")
    try:
        with requests.post(
            f"{endpoint.rstrip('/')}/api/pull",
            json={"model": name, "stream": True},
            stream=True,
            timeout=None,
        ) as r:
            r.raise_for_status()
            last = -1
            for raw in r.iter_lines():
                if not raw:
                    continue
                d = json.loads(raw)
                if "error" in d:
                    log(f"  ✘ erro: {d['error']}")
                    return False
                total = d.get("total")
                done = d.get("completed")
                if total and done:
                    pct = int(done * 100 / total)
                    if pct != last and pct % 10 == 0:
                        last = pct
                        log(f"  {name}: {pct}%")
        log(f"✔ {name} pronto.")
        return True
    except Exception as exc:
        log(f"  ✘ erro baixando {name}: {exc}")
        return False


# ----------------------------------------------------------------------
# Orchestration (runs in a worker thread)
# ----------------------------------------------------------------------

def run_plan(endpoint: str, model_names: List[str], plan: dict, log: LogFn = _noop) -> bool:
    ok = True

    if plan.get("need_chromium"):
        log("── Passo: navegador Chromium ──")
        ok = install_chromium(log) and ok

    exe = plan.get("ollama_exe")
    if plan.get("need_ollama"):
        log("── Passo: instalar Ollama ──")
        exe = install_ollama(log)
        ok = (exe is not None) and ok

    if exe or ollama_exe_path():
        log("── Passo: servidor Ollama ──")
        if not ensure_server(endpoint, exe or ollama_exe_path(), log):
            log("✘ Servidor Ollama indisponível — modelos não serão baixados agora.")
            return False

    # Recompute missing models now that the server is up.
    to_pull = missing_models(endpoint, model_names)
    if to_pull:
        log("── Passo: modelos LLM ──")
        for name in to_pull:
            ok = pull_model(endpoint, name, log) and ok

    log("Provisionamento concluído." if ok else "Provisionamento terminou com avisos.")
    return ok
