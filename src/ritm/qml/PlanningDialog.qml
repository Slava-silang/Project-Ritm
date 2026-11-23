pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RitmDialog {
    id: dialog

    component UiArea: RitmTextArea { appearance: dialog.appearance }

    component UiCheck: RitmCheckBox { appearance: dialog.appearance }
    component UiFooter: RitmDialogFooter { appearance: dialog.appearance }
    objectName: "planningDialog"
    required property var planner
    required property var taskBackend

    component UiButton: RitmButton { appearance: dialog.appearance }
    component UiCombo: RitmComboBox { appearance: dialog.appearance }
    property var state: planner ? planner.state : ({busy: false, tasks: [], hours: [], blocks: []})
    property bool hoursDirty: false
    property bool savingHours: false
    property bool blocksDirty: false
    property var rows: []
    property var blockRows: []
    title: qsTr("Рабочие часы и расписание")
    modal: true
    closePolicy: state.saving ? Popup.NoAutoClose : Popup.CloseOnEscape
    onOpened: {
        hoursDirty = false;
        blocksDirty = false;
        planner.load();
    }
    onClosed: if (planner) planner.cancel()
    background: Rectangle {
        color: dialog.appearance.colors.surface
        border.color: dialog.appearance.colors.border
        radius: dialog.appearance.dialogRadius(14)
    }
    Connections {
        target: dialog.planner
        function onChanged() {
            if (dialog.savingHours && !dialog.state.busy) {
                dialog.hoursDirty = !!dialog.state.error;
                dialog.savingHours = false;
            }
            if (!dialog.state.busy && !dialog.hoursDirty)
                dialog.rows = JSON.parse(JSON.stringify(dialog.state.hours));
            if (!dialog.state.busy && !dialog.blocksDirty)
                dialog.blockRows = JSON.parse(JSON.stringify(dialog.state.blocks));
        }
        function onApplied() {
            dialog.taskBackend.reload();
            dialog.close();
        }
    }
    contentItem: RitmScrollView {
        id: scroll
        appearance: dialog.appearance
        ScrollBar.vertical.objectName: "planningScrollBar"
        ColumnLayout {
            width: scroll.availableWidth
            spacing: dialog.appearance.dp(10)
            SkillPane {
                Layout.fillWidth: true
                library: dialog.planner ? dialog.planner.skills : null
                appearance: dialog.appearance
                prefix: "planning"
                blocked: !!dialog.state.busy
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("Часы указаны по местному времени. По умолчанию — пн–пт, 09:00–18:00; измените их под свой график. Они ограничивают предложения помощника, ручные блоки остаются свободными.")
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
            }
            Repeater {
                id: days
                model: dialog.rows
                delegate: RowLayout {
                    id: dayRow
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    enabled: !dialog.state.busy
                    UiCheck {
                        objectName: "workingDay_" + dayRow.index
                        text: [qsTr("Пн"), qsTr("Вт"), qsTr("Ср"), qsTr("Чт"), qsTr("Пт"), qsTr("Сб"), qsTr("Вс")][dayRow.index]
                        checked: dayRow.modelData.enabled
                        onToggled: { dialog.rows[dayRow.index].enabled = checked; dialog.hoursDirty = true; }
                    }
                    DateTimeField {
                        appearance: dialog.appearance
                        timeOnly: true
                        objectName: "workingStart_" + dayRow.index
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: dayRow.modelData.start
                        placeholderText: "09:00"
                        Accessible.name: qsTr("Начало рабочего времени")
                        onTextEdited: { dialog.rows[dayRow.index].start = text; dialog.hoursDirty = true; }
                    }
                    Label { text: "—" }
                    DateTimeField {
                        appearance: dialog.appearance
                        timeOnly: true
                        allow24: true
                        objectName: "workingEnd_" + dayRow.index
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: dayRow.modelData.end
                        placeholderText: "18:00"
                        Accessible.name: qsTr("Конец рабочего времени")
                        onTextEdited: { dialog.rows[dayRow.index].end = text; dialog.hoursDirty = true; }
                    }
                }
            }
            UiButton {
                objectName: "saveWorkingHours"
                text: qsTr("Сохранить рабочие часы")
                enabled: !dialog.state.busy
                onClicked: {
                    dialog.savingHours = true;
                    dialog.planner.saveHours(dialog.rows);
                }
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: dialog.state.provider === "mock"
                    ? qsTr("Тестовый помощник без сети: заполняет свободные интервалы по порядку.")
                    : qsTr("Используется выбранное подключение ChatGPT. Запрос, выбранная задача, занятость периода без названий других задач и рабочие часы отправятся при нажатии «Предложить». Подключение меняется в окне помощника.")
            }
            UiCombo {
                id: task
                objectName: "planningTask"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                model: dialog.state.tasks
                textRole: "title"
                valueRole: "id"
                enabled: !dialog.state.busy
                onActivated: if (dialog.state.hasDraft) dialog.planner.cancel()
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("Период включительно, до 31 дня · ГГГГ-ММ-ДД")
            }
            RowLayout {
                Layout.fillWidth: true
                enabled: !dialog.state.busy
                DateTimeField {
                    appearance: dialog.appearance
                    dateOnly: true
                    isoDate: true
                    id: first
                    objectName: "planningFirst"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: Qt.formatDateTime(new Date(), "yyyy-MM-dd")
                    Accessible.name: qsTr("Первый день планирования")
                    onTextEdited: if (dialog.state.hasDraft) dialog.planner.cancel()
                }
                DateTimeField {
                    appearance: dialog.appearance
                    dateOnly: true
                    isoDate: true
                    id: last
                    objectName: "planningLast"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: Qt.formatDateTime(new Date(Date.now() + 6 * 86400000), "yyyy-MM-dd")
                    Accessible.name: qsTr("Последний день планирования")
                    onTextEdited: if (dialog.state.hasDraft) dialog.planner.cancel()
                }
            }
            UiArea {
                id: prompt
                objectName: "planningPrompt"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                placeholderText: qsTr("Пожелания к распределению времени")
                wrapMode: TextEdit.Wrap
                textFormat: TextEdit.PlainText
                enabled: !dialog.state.busy
                onTextEdited: if (dialog.state.hasDraft) dialog.planner.cancel()
            }
            UiButton {
                objectName: "planningGenerate"
                text: qsTr("Предложить расписание")
                enabled: !dialog.state.busy && !dialog.hoursDirty && task.currentIndex >= 0
                    && (!dialog.planner || !dialog.planner.skills.state.busy)
                onClicked: {
                    dialog.blocksDirty = false;
                    dialog.planner.generate(task.currentValue, first.text, last.text, prompt.text);
                }
            }
            BusyIndicator { running: dialog.state.busy; visible: running }
            Label {
                Layout.fillWidth: true
                visible: !!dialog.state.error
                text: dialog.state.error || ""
                color: dialog.appearance.colors.warning
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
            }
            Label {
                Layout.fillWidth: true
                visible: dialog.state.hasDraft
                text: dialog.state.summary || ""
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
            }
            Repeater {
                model: dialog.blockRows
                delegate: ColumnLayout {
                    id: blockRow
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    enabled: !dialog.state.busy
                    Label { text: qsTr("Блок ") + (blockRow.index + 1) }
                    DateTimeField {
                        appearance: dialog.appearance
                        allowOffset: true
                        id: blockStart
                        objectName: "planningBlockStart_" + blockRow.index
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: blockRow.modelData.start
                        Accessible.name: qsTr("Начало блока")
                        onTextEdited: { dialog.blockRows[blockRow.index].start = text; dialog.blocksDirty = true; }
                    }
                    DateTimeField {
                        appearance: dialog.appearance
                        allowOffset: true
                        id: blockEnd
                        objectName: "planningBlockEnd_" + blockRow.index
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: blockRow.modelData.end
                        Accessible.name: qsTr("Конец блока")
                        onTextEdited: { dialog.blockRows[blockRow.index].end = text; dialog.blocksDirty = true; }
                    }
                    Flow {
                        Layout.fillWidth: true
                        UiButton {
                            objectName: "planningCheckBlock_" + blockRow.index
                            text: qsTr("Проверить все правки")
                            onClicked: {
                                dialog.planner.editBlocks(dialog.blockRows);
                                dialog.blocksDirty = !!dialog.planner.state.error;
                                if (!dialog.blocksDirty) dialog.blockRows = JSON.parse(JSON.stringify(dialog.state.blocks));
                            }
                        }
                        UiButton {
                            objectName: "planningRemoveBlock_" + blockRow.index
                            text: qsTr("Убрать блок")
                            enabled: !dialog.blocksDirty
                            onClicked: { dialog.planner.removeBlock(blockRow.index); dialog.blocksDirty = false; }
                        }
                    }
                }
            }
        }
    }
    footer: UiFooter {
        UiButton {
            objectName: "planningApply"
            text: qsTr("Подтвердить и добавить блоки")
            enabled: !!dialog.state.canApply && !dialog.blocksDirty && !dialog.hoursDirty
            onClicked: dialog.planner.apply()
        }
        UiButton {
            objectName: "planningClose"
            text: dialog.state.busy ? qsTr("Отменить запрос и закрыть") : qsTr("Закрыть")
            enabled: !dialog.state.saving
            onClicked: dialog.close()
        }
    }
}
