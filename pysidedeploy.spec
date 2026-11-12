[app]
title = ritm
project_dir = .
input_file = src/main.py
exec_directory = dist
project_file =
icon =

[python]
packages = Nuitka,ordered-set,zstandard

[qt]
qml_files = src/ritm/qml/Main.qml,src/ritm/qml/CalendarPane.qml,src/ritm/qml/DayTimeline.qml,src/ritm/qml/StatisticsPane.qml,src/ritm/qml/Appearance.qml
excluded_qml_plugins =
modules = Core,Gui,Qml,Quick,QuickControls2
plugins =

[nuitka]
mode = standalone
extra_args = --noinclude-qt-translations --output-filename=ritm.bin --static-libpython=no --jobs=4 --disable-ccache --report=build/compilation-report.xml
macos.permissions =
