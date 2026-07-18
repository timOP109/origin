"""Application smoke tests."""

from direct_infusion_quant.app import create_application
from direct_infusion_quant.gui.main_window import MainWindow


def test_application_window_smoke(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application = create_application(["direct-infusion-quant-test"])
    window = MainWindow()

    assert application.applicationName() is not None
    assert window.windowTitle() == "DirectInfusionQuant"
    assert window.centralWidget() is not None

    window.close()
