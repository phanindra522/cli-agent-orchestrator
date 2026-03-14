import logging
import os
from datetime import datetime

from cli_agent_orchestrator.constants import LOG_DIR


def setup_logging() -> None:
    """Setup logging configuration."""
    log_level = os.getenv("CAO_LOG_LEVEL", "INFO").upper()

    # Ensure log directory exists
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = LOG_DIR / f"cao_{timestamp}.log"

    format_str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(logging.Formatter(format_str))
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(format_str))
    logging.basicConfig(
        level=log_level,
        format=format_str,
        handlers=[file_handler, console_handler],
    )

    print(f"Server logs: {log_file}")
    print("For debug logs: export CAO_LOG_LEVEL=DEBUG && cao-server")
    logging.info(f"Logging to: {log_file}")
