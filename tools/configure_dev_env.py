"""Configure this .venv's caches outside OneDrive (no global changes)."""
import os

from pathlib import Path
import subprocess
import sys


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    environment = root / ".venv"
    if Path(sys.prefix).resolve() != environment.resolve():
        raise SystemExit("Run with .venv\\Scripts\\python.exe tools\\configure_dev_env.py")
    cache = Path(os.environ["LOCALAPPDATA"]) / "SmartsCCTools" / "cache"
    hook = environment / "Lib/site-packages/00_smartscc_dev_cache.pth"
    pip_config = environment / "pip.ini"
    if "--check" not in sys.argv:
        cache.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "import os, sys; sys.pycache_prefix = os.path.join(os.environ['LOCALAPPDATA'], 'SmartsCCTools', 'cache', 'python')\n",
            encoding="utf-8",
        )
        (hook.parent / "smartscc_dev_cache.pth").unlink(missing_ok=True)
        pip_config.write_text("[global]\ncache-dir = " + str(cache / "pip") + "\n", encoding="utf-8")
    assert hook.is_file() and pip_config.is_file(), "Development cache configuration is missing"
    # Check in a fresh interpreter so the installed .pth is actually exercised.
    prefix = subprocess.check_output(
        [sys.executable, "-c", "import sys; print(sys.pycache_prefix)"], text=True,
    ).strip()
    assert Path(prefix).resolve() == (cache / "python").resolve(), prefix
    pip_cache = subprocess.check_output(
        [sys.executable, "-m", "pip", "cache", "dir"], text=True,
    ).strip()
    assert Path(pip_cache).resolve() == (cache / "pip").resolve(), pip_cache
    print("Environment: " + str(environment))
    print("Cache: " + str(cache))


if __name__ == "__main__":
    main()
