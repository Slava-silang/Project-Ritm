pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    objectName: "calendarPane"
    required property var backend
    property var view: backend.calendar
    property bool narrow: width < 850
    signal openTask(string identifier)
    property bool dragging: false
    property var dragEntry: ({})
    property point dragOrigin: Qt.point(0, 0)

    function beginDrag(entry, point) {
        dragEntry = entry;
        dragOrigin = point;
        dragGhost.x = point.x - 80;
        dragGhost.y = point.y - 18;
    }
    function updateDrag(point) {
        if (Math.abs(point.x - dragOrigin.x) + Math.abs(point.y - dragOrigin.y) > 8)
            dragging = true;
        dragGhost.x = point.x - 80;
        dragGhost.y = point.y - 18;
    }
    function finishDrag(accept) {
        if (dragging && accept)
            dragGhost.Drag.drop();
        dragging = false;
    }
    function beginEdit(entry) {
        blockEditor.entry = entry;
        blockTask.currentIndex = Math.max(0, pane.backend.calendarTasks.findIndex(task => task.id === entry.taskId));
        blockStart.text = entry.startText;
        blockEnd.text = entry.endText;
        blockEditor.open();
    }
    component DragPayload: Rectangle {
        property var entry
    }
    DragPayload {
        id: dragGhost
        parent: Overlay.overlay
        entry: pane.dragEntry
        visible: pane.dragging
        width: 160
        height: 36
        z: 1000
        radius: 8
        color: "#d5e5cc"
        border.color: "#2b5949"
        Drag.active: pane.dragging
        Drag.source: dragGhost
        Drag.keys: ["ritm-work-block"]
        Drag.hotSpot.x: 80
        Drag.hotSpot.y: 18
        Label {
            anchors.fill: parent
            anchors.margins: 6
            textFormat: Text.PlainText
            text: pane.dragEntry.title || ""
            color: "#243d33"
            elide: Text.ElideRight
        }
    }
    spacing: 12

    function beginCreate(taskId) {
        blockEditor.entry = null;
        blockTask.currentIndex = Math.max(0, pane.backend.calendarTasks.findIndex(task => task.id === taskId));
        const day = pane.view.formDate || "";
        blockStart.text = day + " 09:00";
        blockEnd.text = day + " 10:00";
        blockEditor.open();
    }
    Connections {
        target: pane.backend
        function onStateChanged() {
            if (pane.backend.busy)
                pane.finishDrag(false);
        }
        function onActionSucceeded(action: string) {
            if (action === "block_create" || action === "block_change")
                blockEditor.close();
        }
    }
    component EventButton: Button {
        id: eventButton
        required property var entry
        property string location: "week"
        objectName: "calendarEvent_" + entry.id
        Layout.fillWidth: true
        horizontalPadding: 8
        verticalPadding: 8
        contentItem: ColumnLayout {
            spacing: 3
            RowLayout {
                visible: eventButton.entry.kind === "block"
                Layout.fillWidth: true
                Rectangle {
                    Layout.preferredWidth: 28
                    Layout.preferredHeight: 24
                    radius: 5
                    color: "#d4e2cd"
                    Label {
                        anchors.centerIn: parent
                        text: "↔"
                        color: "#2b5949"
                    }
                    MouseArea {
                        objectName: eventButton.location + "DayDrag_" + eventButton.entry.id
                        anchors.fill: parent
                        preventStealing: true
                        enabled: !pane.backend.busy
                        cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                        property bool cancelled: false
                        onPressed: mouse => {
                            forceActiveFocus();
                            cancelled = false;
                            pane.beginDrag(eventButton.entry, mapToItem(Overlay.overlay, mouse.x, mouse.y));
                        }
                        onPositionChanged: mouse => {
                            if (pressed && !cancelled)
                                pane.updateDrag(mapToItem(Overlay.overlay, mouse.x, mouse.y));
                        }
                        onReleased: pane.finishDrag(true)
                        onCanceled: pane.finishDrag(false)
                        Keys.onEscapePressed: {
                            cancelled = true;
                            pane.finishDrag(false);
                        }
                    }
                }
                Item {
                    Layout.fillWidth: true
                }
                ToolButton {
                    objectName: "editBlock_" + eventButton.entry.id
                    width: 25
                    height: 24
                    text: "⋯"
                    Accessible.name: qsTr("Изменить рабочий блок")
                    onClicked: pane.beginEdit(eventButton.entry)
                }
            }
            Label {
                text: (eventButton.entry.kind === "deadline" ? qsTr("◆ Срок · ") : "") + eventButton.entry.time
                color: eventButton.entry.kind === "deadline" ? "#a34425" : "#2b5949"
                font.pixelSize: 12
                font.bold: true
            }
            Label {
                Layout.fillWidth: true
                textFormat: Text.PlainText
                text: eventButton.entry.title
                wrapMode: Text.Wrap
                color: "#243d33"
                font.pixelSize: 13
            }
            Label {
                Layout.fillWidth: true
                visible: eventButton.entry.kind === "block"
                text: eventButton.entry.segmentDuration === eventButton.entry.duration ? eventButton.entry.duration : eventButton.entry.segmentDuration + qsTr(" в этом дне · ") + eventButton.entry.duration + qsTr(" всего")
                wrapMode: Text.Wrap
                color: "#69766d"
                font.pixelSize: 11
            }
            Label {
                Layout.fillWidth: true
                visible: !!eventButton.entry.warning
                text: eventButton.entry.warning
                wrapMode: Text.Wrap
                color: "#a34425"
                font.pixelSize: 11
            }
        }
        background: Rectangle {
            radius: 8
            color: eventButton.entry.kind === "deadline" ? "#fff0e7" : eventButton.hovered ? "#dce9dd" : "#e9f0e5"
            border.color: eventButton.visualFocus ? "#2b5949" : "transparent"
        }
        onClicked: pane.openTask(entry.taskId)
    }

    RowLayout {
        Layout.fillWidth: true
        enabled: !pane.backend.busy
        Button {
            objectName: "calendarPrevButton"
            text: "←"
            Accessible.name: qsTr("Предыдущий период")
            onClicked: pane.backend.navigateCalendar(-1)
        }
        Button {
            objectName: "calendarTodayButton"
            text: qsTr("Сегодня")
            onClicked: pane.backend.calendarToday()
        }
        Button {
            objectName: "calendarNextButton"
            text: "→"
            Accessible.name: qsTr("Следующий период")
            onClicked: pane.backend.navigateCalendar(1)
        }
        Item {
            Layout.fillWidth: true
        }
        Button {
            objectName: "calendarWeekButton"
            text: qsTr("Неделя")
            highlighted: pane.view.mode === "week"
            onClicked: pane.backend.setCalendarMode("week")
        }
        Button {
            objectName: "calendarMonthButton"
            text: qsTr("Месяц")
            highlighted: pane.view.mode === "month"
            onClicked: pane.backend.setCalendarMode("month")
        }
    }
    RowLayout {
        Layout.fillWidth: true
        Label {
            Layout.fillWidth: true
            text: pane.view.title || ""
            font.pixelSize: pane.narrow ? 19 : 24
            font.bold: true
            color: "#243d33"
            wrapMode: Text.Wrap
        }
        Button {
            objectName: "newBlockButton"
            text: qsTr("+ Блок")
            enabled: !pane.backend.busy && pane.backend.calendarTasks.length > 0
            onClicked: pane.beginCreate("")
        }
    }
    Label {
        Layout.fillWidth: true
        text: pane.backend.calendarTasks.length ? qsTr("Зелёный — рабочий блок · ◆ — дедлайн. Событие открывает задачу; ↔ переносит блок между днями. На шкале дня можно двигать блок и тянуть его нижний край.") : qsTr("Сначала добавьте задачу, чтобы запланировать время.")
        wrapMode: Text.Wrap
        color: "#69766d"
        font.pixelSize: 13
    }
    Rectangle {
        Layout.fillWidth: true
        implicitHeight: message.implicitHeight + 20
        visible: !!pane.backend.notice
        color: "#e5efdf"
        radius: 10
        Label {
            id: message
            anchors.fill: parent
            anchors.margins: 10
            text: pane.backend.notice
            wrapMode: Text.Wrap
            color: "#2b5949"
        }
    }
    Timer {
        interval: 40
        repeat: true
        running: pane.dragging
        onTriggered: {
            const point = dragGhost.mapToItem(calendarScroll, dragGhost.width / 2, dragGhost.height / 2);
            const view = calendarScroll.contentItem as Flickable;
            if (!view)
                return;
            if (point.y < 16)
                view.contentY = Math.max(0, view.contentY - 16);
            else if (point.y > calendarScroll.height - 16)
                view.contentY = Math.min(Math.max(0, view.contentHeight - view.height), view.contentY + 16);
        }
    }
    ScrollView {
        id: calendarScroll
        objectName: "calendarScroll"
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: calendarScroll.availableWidth
            spacing: 16
            GridLayout {
                Layout.fillWidth: true
                columns: 7
                columnSpacing: pane.narrow ? 3 : 8
                rowSpacing: 8
                Repeater {
                    model: [qsTr("Пн"), qsTr("Вт"), qsTr("Ср"), qsTr("Чт"), qsTr("Пт"), qsTr("Сб"), qsTr("Вс")]
                    delegate: Label {
                        required property string modelData
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        text: modelData
                        color: "#69766d"
                        horizontalAlignment: Text.AlignHCenter
                    }
                }
                Repeater {
                    model: pane.view.days || []
                    delegate: Rectangle {
                        id: dayCell
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        Layout.alignment: Qt.AlignTop
                        implicitHeight: (pane.view.mode === "month" || pane.narrow) ? (pane.narrow ? 95 : 120) : Math.max(230, dayContent.implicitHeight + 16)
                        radius: 10
                        color: dayCell.modelData.today ? "#eaf0e1" : dayCell.modelData.inMonth || pane.view.mode === "week" ? "#ffffff" : "#eceee6"
                        border.color: dayCell.modelData.selected ? "#2b5949" : "#dfe5d9"
                        border.width: dayCell.modelData.selected ? 2 : 1
                        DropArea {
                            objectName: "calendarDrop_" + dayCell.index
                            anchors.fill: parent
                            keys: ["ritm-work-block"]
                            enabled: !pane.backend.busy
                            onDropped: drop => {
                                const source = drop.source as DragPayload;
                                if (source) {
                                    pane.backend.changeBlock({
                                        id: source.entry.id,
                                        expectedStart: source.entry.startsAt,
                                        expectedEnd: source.entry.endsAt,
                                        operation: "day",
                                        day: dayCell.modelData.date,
                                        sourceDay: source.entry.day
                                    });
                                    drop.acceptProposedAction();
                                }
                            }
                        }
                        ColumnLayout {
                            id: dayContent
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: pane.narrow ? 4 : 8
                            spacing: 5
                            Button {
                                objectName: "calendarDay_" + dayCell.index
                                Layout.fillWidth: true
                                text: dayCell.modelData.number + (dayCell.modelData.today ? " •" : "")
                                flat: true
                                padding: 3
                                enabled: !pane.backend.busy
                                onClicked: pane.backend.selectCalendarDay(dayCell.modelData.date)
                            }
                            Repeater {
                                model: pane.view.mode === "week" && !pane.narrow ? dayCell.modelData.entries : []
                                delegate: EventButton {
                                    required property var modelData
                                    entry: modelData
                                    enabled: !pane.backend.busy
                                }
                            }
                            Label {
                                Layout.fillWidth: true
                                visible: (pane.view.mode === "month" || pane.narrow) && dayCell.modelData.blockCount > 0
                                text: pane.narrow ? "● " + dayCell.modelData.blockCount : qsTr("Блоки: ") + dayCell.modelData.blockCount
                                font.pixelSize: 12
                                color: "#2b5949"
                            }
                            Label {
                                Layout.fillWidth: true
                                visible: (pane.view.mode === "month" || pane.narrow) && dayCell.modelData.deadlineCount > 0
                                text: "◆ " + dayCell.modelData.deadlineCount
                                font.pixelSize: 12
                                color: "#a34425"
                            }
                        }
                    }
                }
            }
            DayTimeline {
                backend: pane.backend
                view: pane.view
                Layout.fillWidth: true
                onEditBlock: entry => pane.beginEdit(entry)
            }
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: agenda.implicitHeight + 28
                radius: 14
                color: "#ffffff"
                ColumnLayout {
                    id: agenda
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 10
                    Label {
                        text: qsTr("Выбранный день · ") + (pane.view.selectedLabel || "")
                        font.pixelSize: 18
                        color: "#243d33"
                        font.bold: true
                    }
                    Label {
                        visible: (pane.view.selectedEvents || []).length === 0
                        text: qsTr("Рабочих блоков и дедлайнов нет")
                        color: "#69766d"
                    }
                    Repeater {
                        model: pane.view.selectedEvents || []
                        delegate: EventButton {
                            location: "agenda"
                            required property var modelData
                            entry: modelData
                            enabled: !pane.backend.busy
                        }
                    }
                }
            }
        }
    }
    Dialog {
        id: blockEditor
        objectName: "blockEditor"
        property var entry: null
        title: entry ? qsTr("Изменить рабочий блок") : qsTr("Запланировать рабочий блок")
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(520, parent ? parent.width - 32 : 520)
        height: Math.min(520, parent ? parent.height - 32 : 520)
        modal: true
        padding: 16
        closePolicy: pane.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        background: Rectangle {
            color: "#ffffff"
            radius: 14
            border.color: "#d0d9cb"
        }
        contentItem: ScrollView {
            id: blockForm
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                width: blockForm.availableWidth
                spacing: 12
                enabled: !pane.backend.busy
                Label {
                    text: qsTr("Задача")
                    color: "#69766d"
                }
                ComboBox {
                    id: blockTask
                    objectName: "blockTaskInput"
                    Layout.fillWidth: true
                    textRole: "title"
                    model: pane.backend.calendarTasks
                    enabled: !blockEditor.entry
                }
                Label {
                    text: qsTr("Начало")
                    color: "#69766d"
                }
                TextField {
                    id: blockStart
                    objectName: "blockStartInput"
                    Layout.fillWidth: true
                    placeholderText: "ДД.ММ.ГГГГ ЧЧ:ММ"
                }
                Label {
                    text: qsTr("Окончание")
                    color: "#69766d"
                }
                TextField {
                    id: blockEnd
                    objectName: "blockEndInput"
                    Layout.fillWidth: true
                    placeholderText: "ДД.ММ.ГГГГ ЧЧ:ММ"
                }
                Label {
                    Layout.fillWidth: true
                    text: qsTr("Местное время. Для блока через полночь укажите следующую дату окончания. Пересечения и превышение срока разрешены с предупреждением.")
                    wrapMode: Text.Wrap
                    color: "#69766d"
                }
                Label {
                    Layout.fillWidth: true
                    visible: !!pane.backend.error
                    text: pane.backend.error
                    wrapMode: Text.Wrap
                    color: "#a34425"
                }
            }
        }
        footer: RowLayout {
            Item {
                Layout.fillWidth: true
            }
            Button {
                objectName: "cancelBlockButton"
                text: qsTr("Отмена")
                enabled: !pane.backend.busy
                onClicked: blockEditor.close()
            }
            Button {
                objectName: "saveBlockButton"
                text: qsTr("Сохранить")
                enabled: !pane.backend.busy && blockTask.currentIndex >= 0
                onClicked: {
                    if (blockEditor.entry)
                        pane.backend.changeBlock({
                            id: blockEditor.entry.id,
                            expectedStart: blockEditor.entry.startsAt,
                            expectedEnd: blockEditor.entry.endsAt,
                            operation: "fields",
                            start: blockStart.text,
                            end: blockEnd.text
                        });
                    else
                        pane.backend.createBlock({
                            taskId: pane.backend.calendarTasks[blockTask.currentIndex].id,
                            start: blockStart.text,
                            end: blockEnd.text
                        });
                }
            }
        }
    }
}
