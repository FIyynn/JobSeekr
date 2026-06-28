from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import undetected_chromedriver as uc


def _vlog(verbose: bool, message: str) -> None:
    if verbose:
        print(message, flush=True)


def build_driver(
    browser_config: dict[str, Any],
    profile_dir: str | Path | None = None,
    verbose: bool = True,
):
    _vlog(verbose, "driver: start")
    options = uc.ChromeOptions()
    if browser_config.get("headless"):
        options.add_argument("--headless=new")
    if browser_config.get("start_maximized", True):
        options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-popup-blocking")
    extensions = browser_config.get("extensions") or []
    if extensions:
        resolved_extensions = [
            str(Path(extension).expanduser().resolve())
            for extension in extensions
        ]
        options.add_argument(f"--load-extension={','.join(resolved_extensions)}")
    user_data_dir = profile_dir or browser_config.get("user_data_dir")
    if user_data_dir:
        resolved_user_data_dir = Path(user_data_dir).expanduser().resolve()
        resolved_user_data_dir.mkdir(parents=True, exist_ok=True)
        options.add_argument(f"--user-data-dir={resolved_user_data_dir}")
    binary_path = browser_config.get("browser_binary_path")
    if binary_path:
        options.binary_location = binary_path
    _vlog(verbose, "driver: launch chrome")
    chrome_kwargs: dict[str, Any] = {
        "options": options,
        "use_subprocess": True,
    }
    version_main = browser_config.get("version_main")
    if version_main is not None:
        chrome_kwargs["version_main"] = int(version_main)
    driver = uc.Chrome(**chrome_kwargs)
    driver.set_page_load_timeout(browser_config.get("page_load_timeout_seconds", 15))
    _vlog(verbose, "driver: ready")
    return driver


def set_zoom(
    driver,
    zoom_percent: int,
    delay_seconds: float | int = 0,
    verbose: bool = True,
) -> dict[str, Any]:
    percent = int(zoom_percent)
    _vlog(verbose, f"zoom: {percent}%")
    try:
        driver.execute_script(
            "document.documentElement.style.zoom = '100%'; document.body.style.zoom = '100%';"
        )
    except Exception:
        pass
    try:
        if percent == 100:
            driver.execute_cdp_cmd("Emulation.resetPageScaleFactor", {})
        else:
            driver.execute_cdp_cmd(
                "Emulation.setPageScaleFactor",
                {"pageScaleFactor": percent / 100.0},
            )
    except Exception:
        driver.execute_script(
            "document.body.style.zoom = arguments[0] + '%';",
            percent,
        )
    if delay_seconds and float(delay_seconds) > 0:
        _vlog(verbose, f"zoom: wait {delay_seconds}s")
        time.sleep(float(delay_seconds))
    _vlog(verbose, "zoom: done")
    return {"zoom_percent": percent}


def close_driver(driver, verbose: bool = True) -> None:
    if driver is None:
        return
    _vlog(verbose, "driver: close")
    try:
        driver.quit()
    except Exception:
        pass
