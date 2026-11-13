pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    objectName: "calendarPane"
    required property var backend
    required property var appearance
    property var view: backend.calendar
    property bool narrow: width < pane.appearance.dp(850)
    signal openTask(string identifier)
    property bool dragging: false
    property var dragEntry: ({})
    property point dragOrigin: Qt.point(0, 0)

    function beginDrag(entry, point) {
        dragEntry = entry;
        dragOrigin = point;
        dragGhost.x = point.x - pane.appearance.dp(80);
        dragGhost.y = point.y - pane.appearance.dp(18);
    }
    function updateDrag(point) {
        if (Math.abs(point.x - dragOrigin.x) + Math.abs(point.y - dragOrigin.y) > 8)
            dragging = true;
        dragGhost.x = point.x - pane.appearance.dp(80);
        dragGhost.y = point.y - pane.appearance.dp(18);
    }
    function finishDrag(accept) {
        if (dragging && accept)
            dragGhost.Drag.drop();
        dragging = false;
    }
    function beginEdit(entry) {
        blockEditor.entry = entry;
        blockKind.currentIndex = entry.isEvent ? 1 : 0;
        eventTitle.text = entry.eventTitle || "";
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
        width: pane.appearance.dp(160)
        height: pane.appearance.dp(36)
        z: 1000
        radius: pane.appearance.dp(8)
        color: pane.appearance.colors.selection
        border.color: pane.appearance.colors.accent
        Drag.active: pane.dragging
        Drag.source: dragGhost
        Drag.keys: ["ritm-work-block"]
        Drag.hotSpot.x: pane.appearance.dp(80)
        Drag.hotSpot.y: pane.appearance.dp(18)
        Label {
            anchors.fill: parent
            anchors.margins: pane.appearance.dp(6)
            textFormat: Text.PlainText
            text: pane.dragEntry.title || ""
            color: pane.appearance.colors.text
            elide: Text.ElideRight
        }
    }
    spacing: pane.appearance.dp(12)

    function beginCreate(taskId) {
        blockEditor.entry = null;
        blockKind.currentIndex = pane.backend.calendarTasks.length ? 0 : 1;
        eventTitle.text = "";
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
        horizontalPadding: pane.appearance.dp(8)
        verticalPadding: pane.appearance.dp(8)
        contentItem: ColumnLayout {
            spacing: pane.appearance.dp(3)
            RowLayout {
                visible: eventButton.entry.kind !== "deadline"
                Layout.fillWidth: true
                Rectangle {
                    Layout.preferredWidth: pane.appearance.dp(28)
                    Layout.preferredHeight: pane.appearance.dp(24)
                    radius: pane.appearance.dp(5)
                    color: pane.appearance.colors.selection
                    Label {
                        anchors.centerIn: parent
                        text: "↔"
                        color: pane.appearance.colors.accent
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
                    width: pane.appearance.dp(25)
                    height: pane.appearance.dp(24)
                    text: "⋯"
                    Accessible.name: qsTr("Изменить рабочий блок")
                    onClicked: pane.beginEdit(eventButton.entry)
                }
            }
            Label {
                text: (eventButton.entry.kind === "deadline" ? qsTr("◆ Срок · ") : "") + eventButton.entry.time
                color: eventButton.entry.kind === "deadline" ? pane.appearance.colors.warning : pane.appearance.colors.accent
                font.pixelSize: pane.appearance.dp(12)
                font.bold: true
            }
            Label {
                Layout.fillWidth: true
                textFormat: Text.PlainText
                text: eventButton.entry.title
                wrapMode: Text.Wrap
                color: pane.appearance.colors.text
                font.pixelSize: pane.appearance.dp(13)
            }
            Label {
                Layout.fillWidth: true
                visible: eventButton.entry.kind !== "deadline"
                text: eventButton.entry.segmentDuration === eventButton.entry.duration ? eventButton.entry.duration : eventButton.entry.segmentDuration + qsTr(" в этом дне · ") + eventButton.entry.duration + qsTr(" всего")
                wrapMode: Text.Wrap
                color: pane.appearance.colors.muted
                font.pixelSize: pane.appearance.dp(11)
            }
            Label {
                Layout.fillWidth: true
                visible: !!eventButton.entry.warning
                text: eventButton.entry.warning
                wrapMode: Text.Wrap
                color: pane.appearance.colors.warning
                font.pixelSize: pane.appearance.dp(11)
            }
        }
        background: Rectangle {
            radius: pane.appearance.dp(8)
            color: eventButton.entry.kind === "deadline" ? pane.appearance.colors.warningSurface : eventButton.entry.isEvent ? (eventButton.hovered ? pane.appearance.colors.eventHover : pane.appearance.colors.event) : eventButton.hovered ? pane.appearance.colors.selection : pane.appearance.colors.work
            border.color: eventButton.visualFocus ? pane.appearance.colors.accent : "transparent"
        }
        onClicked: {
            if (entry.kind === "event")
                pane.beginEdit(entry);
            else
                pane.openTask(entry.taskId);
        }
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
            font.pixelSize: pane.narrow ? pane.appearance.dp(19) : pane.appearance.dp(24)
            font.bold: true
            color: pane.appearance.colors.text
            wrapMode: Text.Wrap
        }
        Button {
            objectName: "newBlockButton"
            text: qsTr("+ В расписание")
            enabled: !pane.backend.busy
            onClicked: pane.beginCreate("")
        }
    }
    Label {
        Layout.fillWidth: true
        text: qsTr("Зелёный — работа над задачей · Фиолетовый — событие · ◆ — дедлайн. ↔ переносит запись между днями.")
        wrapMode: Text.Wrap
        color: pane.appearance.colors.muted
        font.pixelSize: pane.appearance.dp(13)
    }
    Rectangle {
        Layout.fillWidth: true
        implicitHeight: message.implicitHeight + pane.appearance.dp(20)
        visible: !!pane.backend.notice
        color: pane.appearance.colors.work
        radius: pane.appearance.dp(10)
        Label {
            id: message
            anchors.fill: parent
            anchors.margins: pane.appearance.dp(10)
            text: pane.backend.notice
            wrapMode: Text.Wrap
            color: pane.appearance.colors.accent
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
            spacing: pane.appearance.dp(16)
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
                        Layout.preferredWidth: pane.appearance.dp(0)
                        text: modelData
                        color: pane.appearance.colors.muted
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
                        Layout.preferredWidth: pane.appearance.dp(0)
                        Layout.alignment: Qt.AlignTop
                        implicitHeight: (pane.view.mode === "month" || pane.narrow) ? pane.appearance.dp(pane.narrow ? 95 : 120) : Math.max(pane.appearance.dp(230), dayContent.implicitHeight + pane.appearance.dp(16))
                        radius: pane.appearance.dp(10)
                        color: dayCell.modelData.today ? pane.appearance.colors.selection : dayCell.modelData.inMonth || pane.view.mode === "week" ? pane.appearance.colors.surface : pane.appearance.colors.panel
                        border.color: dayCell.modelData.selected ? pane.appearance.colors.accent : pane.appearance.colors.border
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
                                        expectedTitle: source.entry.eventTitle,
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
                            anchors.margins: pane.appearance.dp(pane.narrow ? 4 : 8)
                            spacing: pane.appearance.dp(5)
                            Button {
                                objectName: "calendarDay_" + dayCell.index
                                Layout.fillWidth: true
                                text: dayCell.modelData.number + (dayCell.modelData.today ? " •" : "")
                                flat: true
                                padding: pane.appearance.dp(3)
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
                                font.pixelSize: pane.appearance.dp(12)
                                color: pane.appearance.colors.accent
                            }
                            Label {
                                Layout.fillWidth: true
                                visible: (pane.view.mode === "month" || pane.narrow) && dayCell.modelData.eventCount > 0
                                text: pane.narrow ? "С: " + dayCell.modelData.eventCount : qsTr("События: ") + dayCell.modelData.eventCount
                                font.pixelSize: pane.appearance.dp(12)
                                color: pane.appearance.colors.eventAccent
                            }
                            Label {
                                Layout.fillWidth: true
                                visible: (pane.view.mode === "month" || pane.narrow) && dayCell.modelData.deadlineCount > 0
                                text: "◆ " + dayCell.modelData.deadlineCount
                                font.pixelSize: pane.appearance.dp(12)
                                color: pane.appearance.colors.warning
                            }
                        }
                    }
                }
            }
            DayTimeline {
                appearance: pane.appearance
                backend: pane.backend
                view: pane.view
                Layout.fillWidth: true
                onEditBlock: entry => pane.beginEdit(entry)
            }
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: agenda.implicitHeight + pane.appearance.dp(28)
                radius: pane.appearance.dp(14)
                color: pane.appearance.colors.surface
                ColumnLayout {
                    id: agenda
                    anchors.fill: parent
                    anchors.margins: pane.appearance.dp(14)
                    spacing: pane.appearance.dp(10)
                    Label {
                        text: qsTr("Выбранный день · ") + (pane.view.selectedLabel || "")
                        font.pixelSize: pane.appearance.dp(18)
                        color: pane.appearance.colors.text
                        font.bold: true
                    }
                    Label {
                        visible: (pane.view.selectedEvents || []).length === 0
                        text: qsTr("Рабочих блоков и дедлайнов нет")
                        color: pane.appearance.colors.muted
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
        title: entry ? (entry.isEvent ? qsTr("Изменить событие") : qsTr("Изменить рабочий блок")) : qsTr("Добавить в расписание")
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(pane.appearance.dp(520), parent ? parent.width - 32 : 520)
        height: Math.min(pane.appearance.dp(520), parent ? parent.height - 32 : 520)
        modal: true
        padding: pane.appearance.dp(16)
        closePolicy: pane.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        background: Rectangle {
            color: pane.appearance.colors.surface
            radius: pane.appearance.dp(14)
            border.color: pane.appearance.colors.border
        }
        contentItem: ScrollView {
            id: blockForm
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                width: blockForm.availableWidth
                spacing: pane.appearance.dp(12)
                enabled: !pane.backend.busy
                Label {
                    text: qsTr("Тип записи")
                    color: pane.appearance.colors.muted
                }
                ComboBox {
                    id: blockKind
                    objectName: "blockKindInput"
                    Layout.fillWidth: true
                    model: [qsTr("Блок задачи"), qsTr("Событие")]
                    enabled: !blockEditor.entry
                }
                Label {
                    text: qsTr("Название события *")
                    color: pane.appearance.colors.muted
                    visible: blockKind.currentIndex === 1
                }
                TextField {
                    id: eventTitle
                    objectName: "eventTitleInput"
                    Layout.fillWidth: true
                    visible: blockKind.currentIndex === 1
                    placeholderText: qsTr("Например, пара или прогулка с друзьями")
                }
                Label {
                    visible: blockKind.currentIndex === 0
                    text: qsTr("Задача")
                    color: pane.appearance.colors.muted
                }
                ComboBox {
                    id: blockTask
                    visible: blockKind.currentIndex === 0
                    objectName: "blockTaskInput"
                    Layout.fillWidth: true
                    textRole: "title"
                    model: pane.backend.calendarTasks
                    enabled: !blockEditor.entry
                }
                Label {
                    text: qsTr("Начало")
                    color: pane.appearance.colors.muted
                }
                TextField {
                    id: blockStart
                    objectName: "blockStartInput"
                    Layout.fillWidth: true
                    placeholderText: "ДД.ММ.ГГГГ ЧЧ:ММ"
                }
                Label {
                    text: qsTr("Окончание")
                    color: pane.appearance.colors.muted
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
                    color: pane.appearance.colors.muted
                }
                Label {
                    Layout.fillWidth: true
                    visible: !!pane.backend.error
                    text: pane.backend.error
                    wrapMode: Text.Wrap
                    color: pane.appearance.colors.warning
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
                enabled: !pane.backend.busy && (blockKind.currentIndex === 1 || (blockTask.currentIndex >= 0 && blockTask.currentIndex < pane.backend.calendarTasks.length))
                onClicked: {
                    if (blockEditor.entry)
                        pane.backend.changeBlock({
                            id: blockEditor.entry.id,
                            expectedStart: blockEditor.entry.startsAt,
                            expectedEnd: blockEditor.entry.endsAt,
                            expectedTitle: blockEditor.entry.eventTitle,
                            title: eventTitle.text,
                            operation: "fields",
                            start: blockStart.text,
                            end: blockEnd.text
                        });
                    else
                        pane.backend.createBlock({
                            kind: blockKind.currentIndex === 1 ? "event" : "task",
                            title: eventTitle.text,
                            taskId: blockKind.currentIndex === 1 ? "" : pane.backend.calendarTasks[blockTask.currentIndex].id,
                            start: blockStart.text,
                            end: blockEnd.text
                        });
                }
            }
        }
    }
}
