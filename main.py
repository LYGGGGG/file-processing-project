import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from exporter import export_by_direction
from fetcher import fetch_all_pages
from mailer import send_alert_email, send_direction_email
from processor import process_records


def load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def substitute_env(value: Any) -> Any:
    if isinstance(value, str):
        out = value
        for key, env_val in os.environ.items():
            out = out.replace(f"${{{key}}}", env_val)
        return out
    if isinstance(value, list):
        return [substitute_env(item) for item in value]
    if isinstance(value, dict):
        return {k: substitute_env(v) for k, v in value.items()}
    return value


def load_config(config_path: Path) -> Dict[str, Any]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    return substitute_env(raw)


def setup_logging(config: Dict[str, Any]) -> None:
    log_cfg = config["logging"]
    log_dir = Path(log_cfg["dir"])
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / log_cfg["file"]
    level = getattr(logging, log_cfg.get("level", "INFO"))

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    load_env(base_dir / ".env")
    config = load_config(base_dir / "config.yaml")
    setup_logging(config)

    logger = logging.getLogger("main")
    logger.info("Start batch process")

    try:
        records = fetch_all_pages(config)
        logger.info("Fetched %s records", len(records))

        processed = process_records(records, config)
        outputs = export_by_direction(processed, config)

        for direction, file_path in outputs.items():
            send_direction_email(direction, file_path, config)

        logger.info("Process completed")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Process failed: %s", exc)
        send_alert_email(str(exc), config)
        raise


if __name__ == "__main__":
    main()
