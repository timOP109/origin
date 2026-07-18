"""Application entry point."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    """Create or return the Qt application instance."""

    from PySide6.QtWidgets import QApplication

    existing = QApplication.instance()
    if existing is not None:
        return existing
    return QApplication(list(argv) if argv is not None else sys.argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Launch DirectInfusionQuant."""

    arguments = list(argv) if argv is not None else sys.argv
    if len(arguments) >= 2 and arguments[1] == "--pyopenms-worker":
        from direct_infusion_quant.io._pyopenms_worker import run_operation

        return run_operation(arguments[2], arguments[3])
    if len(arguments) >= 2 and arguments[1] == "--packaging-smoke":
        from direct_infusion_quant.packaging_smoke import run_from_arguments

        return run_from_arguments(arguments[2:])

    from direct_infusion_quant.gui.main_window import MainWindow
    from direct_infusion_quant.logging_config import configure_logging

    configure_logging()
    logging.getLogger(__name__).info("application_starting")
    application = create_application(arguments)
    window = MainWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
