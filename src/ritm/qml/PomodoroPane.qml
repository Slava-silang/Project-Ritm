pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    objectName: "pomodoroPane"
    required property var backend
    required property var appearance
    readonly property var snapshot: backend.pomodoro
    readonly property bool compact: width < appearance.dp(700)
    readonly property bool dialogsOpen: editor.visible || stopDialog.visible
    spacing: appearance.dp(3)

    RowLayout {
        Layout.fillWidth: true
        Button {
            objectName: "pomodoroOpenButton"
            text: qsTr("Pomodoro") + (pane.compact && pane.snapshot.active ? " · "
                + (pane.snapshot.reached ? qsTr("Интервал завершён") : pane.snapshot.remaining) : "")
            highlighted: !!pane.snapshot.reached
            enabled: !pane.backend.busy && !pane.backend.focus.recovery
            onClicked: editor.begin()
        }
        Label {
            Layout.fillWidth: true
            visible: !!pane.snapshot.active && !pane.compact
            text: pane.snapshot.label + (pane.width < pane.appearance.dp(700) ? "\n" : " · ")
                + (pane.snapshot.remaining || "00:00:00") + (pane.snapshot.phase === "work"
                ? qsTr(" · интервал ") + (pane.snapshot.cycle + 1) + "/4" : "")
                + (!pane.snapshot.running && !pane.snapshot.reached ? qsTr(" · на паузе") : "")
            wrapMode: Text.WordWrap
        }
        Button {
            objectName: "pomodoroStopButton"
            visible: !!pane.snapshot.active && !pane.compact
            enabled: !pane.backend.busy && !pane.backend.focus.recovery
            text: qsTr("Завершить")
            onClicked: stopDialog.open()
        }
    }
    Label {
        objectName: "pomodoroNotice"
        Layout.fillWidth: true
        visible: !!pane.snapshot.active && !!pane.snapshot.reached && !pane.compact
        wrapMode: Text.Wrap
        text: pane.snapshot.phase === "work"
            ? qsTr("Рабочий интервал завершён. Переработка учитывается: ") + pane.snapshot.overtime
            : qsTr("Перерыв завершён. Работа начнётся после нажатия «Начать работу».")
    }
    Flow {
        Layout.fillWidth: true
        visible: !!pane.snapshot.active && !pane.compact
        spacing: pane.appearance.dp(5)
        Button {
            objectName: "pomodoroBreakButton"
            visible: pane.snapshot.phase === "work" && !!pane.snapshot.reached
            text: qsTr("Начать перерыв")
            enabled: !pane.backend.busy && !pane.backend.focus.recovery
            onClicked: pane.backend.changePomodoro("break")
        }
        Button {
            objectName: "pomodoroWorkButton"
            visible: pane.snapshot.phase !== "work" && !!pane.snapshot.reached
                || pane.snapshot.phase === "work" && !pane.backend.focus.elapsed
            text: qsTr("Начать работу")
            enabled: !pane.backend.busy && !pane.backend.focus.recovery
            onClicked: editor.begin()
        }
        Button {
            objectName: "pomodoroBreakPause"
            visible: pane.snapshot.phase === "short" || pane.snapshot.phase === "long"
            text: pane.snapshot.running ? qsTr("Пауза перерыва") : qsTr("Продолжить перерыв")
            enabled: !pane.backend.busy && !pane.snapshot.reached
            onClicked: pane.backend.changePomodoro(pane.snapshot.running ? "pause" : "resume")
        }
    }

    Dialog {
        id: editor
        objectName: "pomodoroDialog"
        property string selectedTask: ""
        property bool attempted: false
        readonly property var tasks: pane.backend.calendarTasks
        title: qsTr("Pomodoro")
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(pane.appearance.dp(480), parent.width - 24)
        height: Math.min(implicitHeight, parent.height - 24)
        modal: true
        closePolicy: pane.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        function begin() {
            attempted = false;
            selectedTask = tasks.some(t => t.id === pane.backend.detail.id)
                ? pane.backend.detail.id : tasks.length ? tasks[0].id : "";
            work.value = pane.snapshot.work;
            shortBreak.value = pane.snapshot.short;
            longBreak.value = pane.snapshot.long;
            open();
        }
        contentItem: ScrollView {
            clip: true
            implicitHeight: form.implicitHeight
            contentWidth: availableWidth
            ColumnLayout {
                id: form
                width: parent.width
                Label {
                    Layout.fillWidth: true
                    visible: !!pane.snapshot.active
                    wrapMode: Text.Wrap
                    text: pane.snapshot.label + " · " + pane.snapshot.remaining
                        + (pane.snapshot.reached ? pane.snapshot.phase === "work"
                            ? qsTr(" · Можно начать перерыв. Переработка: ") + pane.snapshot.overtime
                            : qsTr(" · Можно начать работу.") : "")
                }
                Flow {
                    Layout.fillWidth: true
                    visible: pane.compact && !!pane.snapshot.active
                    spacing: pane.appearance.dp(4)
                    Button {
                        objectName: "pomodoroDialogBreakButton"
                        visible: pane.snapshot.phase === "work" && !!pane.snapshot.reached
                        text: qsTr("Начать перерыв")
                        enabled: !pane.backend.busy && !pane.backend.focus.recovery
                        onClicked: pane.backend.changePomodoro("break")
                    }
                    Button {
                        objectName: "pomodoroDialogPauseButton"
                        visible: pane.snapshot.phase === "short" || pane.snapshot.phase === "long"
                        text: pane.snapshot.running ? qsTr("Пауза перерыва") : qsTr("Продолжить перерыв")
                        enabled: !pane.backend.busy && !pane.snapshot.reached
                        onClicked: pane.backend.changePomodoro(pane.snapshot.running ? "pause" : "resume")
                    }
                    Button {
                        text: qsTr("Завершить цикл")
                        enabled: !pane.backend.busy && !pane.backend.focus.recovery
                        onClicked: { editor.close(); stopDialog.open(); }
                    }
                }
                Label {
                    Layout.fillWidth: true
                    text: qsTr("Задача для рабочего интервала")
                    wrapMode: Text.Wrap
                }
                ComboBox {
                    id: taskChoice
                    objectName: "pomodoroTaskChoice"
                    Layout.fillWidth: true
                    model: editor.tasks
                    textRole: "title"
                    valueRole: "id"
                    currentIndex: editor.tasks.findIndex(t => t.id === editor.selectedTask)
                    onActivated: editor.selectedTask = currentValue
                }
                Label {
                    visible: !editor.tasks.length
                    text: qsTr("Сначала создайте незавершённую задачу.")
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                }
                GridLayout {
                    columns: 2
                    enabled: !pane.snapshot.active
                    Label { text: qsTr("Работа, мин") }
                    SpinBox { id: work; objectName: "pomodoroWorkMinutes"; from: 1; to: 240; editable: true }
                    Label { text: qsTr("Перерыв, мин") }
                    SpinBox { id: shortBreak; objectName: "pomodoroShortMinutes"; from: 1; to: 240; editable: true }
                    Label { text: qsTr("Длинный, мин") }
                    SpinBox { id: longBreak; objectName: "pomodoroLongMinutes"; from: 1; to: 240; editable: true }
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: qsTr("Длинный перерыв после четырёх интервалов. Все переходы — вручную. Цикл общий для задач.")
                }
                Label {
                    Layout.fillWidth: true
                    visible: !!pane.backend.focus.elapsed
                    wrapMode: Text.Wrap
                    text: qsTr("Текущая фокус-сессия завершится и сохранится в истории. Цикл Pomodoro сохранится.")
                }
                Label {
                    Layout.fillWidth: true
                    visible: editor.attempted && !!pane.backend.error
                    text: pane.backend.error
                    wrapMode: Text.Wrap
                }
            }
        }
        footer: DialogButtonBox {
            Button {
                text: qsTr("Отмена")
                enabled: !pane.backend.busy
                onClicked: editor.close()
            }
            Button {
                objectName: "pomodoroConfirmButton"
                text: qsTr("Начать работу")
                enabled: !pane.backend.busy && taskChoice.currentIndex >= 0
                    && (!pane.snapshot.active || pane.snapshot.phase === "work" || pane.snapshot.reached)
                onClicked: {
                    editor.attempted = true;
                    pane.backend.startPomodoro(editor.selectedTask, work.value, shortBreak.value,
                        longBreak.value, !!pane.backend.focus.elapsed);
                }
            }
        }
    }
    Dialog {
        id: stopDialog
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(pane.appearance.dp(430), parent.width - 24)
        title: qsTr("Завершить цикл Pomodoro?")
        modal: true
        closePolicy: pane.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        contentItem: ColumnLayout {
            Label {
                Layout.fillWidth: true
                text: qsTr("Текущая работа сохранится в истории. Счётчик интервалов сбросится; длительности останутся.")
                wrapMode: Text.Wrap
            }
        }
        footer: DialogButtonBox {
            Button { text: qsTr("Отмена"); enabled: !pane.backend.busy; onClicked: stopDialog.close() }
            Button {
                objectName: "pomodoroConfirmStop"
                text: qsTr("Завершить цикл")
                enabled: !pane.backend.busy
                onClicked: pane.backend.changePomodoro("stop")
            }
        }
    }
    Connections {
        target: pane.backend
        function onActionSucceeded(action) {
            if (action === "pomodoro") {
                editor.close();
                stopDialog.close();
            }
        }
    }
}
