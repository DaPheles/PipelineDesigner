"""Application entry point."""

import sys

from PySide6.QtWidgets import QApplication

from pipeline_designer.app import AppConfig, MainWindow


def main() -> int:
    """Run the Pipeline Designer application.

    Returns:
        Exit code.
    """
    app = QApplication(sys.argv)
    app.setApplicationName("Pipeline Designer")
    app.setOrganizationName("PipelineDesigner")

    app.setStyle("Fusion")

    config = AppConfig.load()
    window = MainWindow(config)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
