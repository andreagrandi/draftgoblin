[app]
# This checked-in spec is for unsigned development bundles only.
title = Draftgoblin-unsigned-windows
project_dir = .
input_file = draftgoblin/qt_gui.py
exec_directory = dist-native/windows-unsigned
project_file = pyproject.toml
icon = draftgoblin/assets/draftgoblin.ico

[python]
# Leave interpreter selection to the uv environment invoking pyside6-deploy.
python_path =
# Keep the Nuitka deployment tool explicit and pin its version in CI/local setup.
packages = Nuitka==4.1.3

[qt]
# Every QML file and the module manifest are listed deliberately. The deploy tool
# also preserves draftgoblin/assets/ so NavigationRail.qml can resolve the logo.
qml_files = draftgoblin/qml/AppBar.qml,draftgoblin/qml/BacktestView.qml,draftgoblin/qml/BuildView.qml,draftgoblin/qml/CardPreview.qml,draftgoblin/qml/LiveDraftView.qml,draftgoblin/qml/Main.qml,draftgoblin/qml/NavigationRail.qml,draftgoblin/qml/PoolSummaryPanel.qml,draftgoblin/qml/RecommendationRow.qml,draftgoblin/qml/SettingsView.qml,draftgoblin/qml/StateBanner.qml,draftgoblin/qml/StatusStrip.qml,draftgoblin/qml/Theme.qml,draftgoblin/qml/qmldir
excluded_qml_plugins = QtCharts,QtQuick3D,QtSensors,QtTest,QtWebEngine
modules = Core,Gui,Qml,Quick,QuickControls2
plugins = imageformats,platforms,platformthemes,styles

[nuitka]
mode = onefile
extra_args = --quiet --noinclude-qt-translations --company-name=Draftgoblin --product-name=Draftgoblin --file-version=0.2.0 --product-version=0.2.0 --file-description="An unofficial Quick Draft assistant for MTG Arena"
macos.permissions =

# Fonts: none are bundled. The application intentionally uses Qt's system fixed
# font so the bundle has no machine-specific font input.
