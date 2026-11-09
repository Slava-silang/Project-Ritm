pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window
    required property var backend
    objectName: "mainWindow"
    width: 1120
    height: 780
    minimumWidth: 640
    minimumHeight: 480
    visible: true
    title: qsTr("Ритм — задачи")
    color: "#f4f3ed"
    font.family: "Sans Serif"
    palette.highlight: "#2b5949"
    palette.button: "#e9eee7"
    palette.buttonText: "#234237"
    palette.text: "#243d33"
    palette.windowText: "#243d33"
    palette.accent: "#2b5949"
    palette.base: "#ffffff"
    palette.window: "#ffffff"
    palette.placeholderText: "#718073"
    property var current: window.backend.detail
    property var activeFocus: window.backend.focus
    property bool hasTask: !!current.id
    property bool narrow: width < 850
    property bool detailOpen: true
    property string page: "tasks"
    property bool closeWhenIdle: false

    onClosing: event => {
        if (window.backend.busy) {
            event.accepted = false;
            window.closeWhenIdle = true;
        } else if (window.activeFocus.elapsed && !window.activeFocus.recovery) {
            event.accepted = false;
            exitFocusDialog.open();
        }
    }

    property var categoryOptions: [
        {
            id: "",
            title: "Все категории"
        },
        {
            id: "none",
            title: "Без категории"
        }
    ].concat(window.backend.categories)

    function toggleTaskTree(identifier) {
        const position = taskList.contentY;
        window.backend.toggleExpanded(identifier);
        Qt.callLater(function () {
            taskList.contentY = Math.max(0, Math.min(position, taskList.contentHeight - taskList.height));
        });
    }
    function filterList() {
        window.backend.setFilters(searchInput.text, ["active", "completed", "all"][statusFilter.currentIndex], window.backend.categoryFilter);
    }
    function askDelete(data) {
        deleteEditor.nodeId = data.id;
        deleteEditor.nodeTitle = data.title;
        deleteEditor.open();
    }
    property real savedScrollY: 0
    property string lastTaskId: ""

    component AccentButton: Button {
        id: actionButton
        horizontalPadding: 18
        verticalPadding: 11
        contentItem: Label {
            text: actionButton.text
            color: "white"
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 9
            color: !actionButton.enabled ? "#9eafa2" : actionButton.down ? "#1d4235" : actionButton.hovered ? "#386b57" : "#2b5949"
            border.width: actionButton.visualFocus ? 2 : 0
            border.color: "#82aa8e"
        }
    }
    component TaskProgress: ProgressBar {
        id: taskProgress
        from: 0
        to: 100
        background: null
        contentItem: Item {
            implicitHeight: 6
            Rectangle {
                anchors.fill: parent
                radius: 3
                color: "#e0e7dc"
            }
            Rectangle {
                width: parent.width * taskProgress.position
                height: parent.height
                radius: 3
                color: "#2b5949"
            }
        }
    }
    component EditorDialog: Dialog {
        padding: 16
        background: Rectangle {
            color: "#ffffff"
            radius: 14
            border.color: "#d0d9cb"
        }
    }
    component Caption: Label {
        color: "#69766d"
        font.pixelSize: 13
    }

    component WeightEntry: RowLayout {
        id: weightRow
        required property var modelData
        required property int index
        property alias value: weightInput.text
        Layout.fillWidth: true
        Label {
            Layout.fillWidth: true
            textFormat: Text.PlainText
            text: weightRow.modelData.title
            wrapMode: Text.Wrap
        }
        TextField {
            id: weightInput
            objectName: "weightInput_" + weightRow.index
            Layout.preferredWidth: 85
            text: weightRow.modelData.weight
            inputMethodHints: Qt.ImhFormattedNumbersOnly
        }
        Caption {
            text: "%"
        }
    }

    function editTask(data) {
        taskEditor.taskId = data.id || "";
        titleInput.text = data.title || "";
        descriptionInput.text = data.description || "";
        difficultyInput.text = data.difficulty || "";
        minutesInput.text = data.minutes || "";
        deadlineInput.text = data.deadline || "";
        formCategory.currentIndex = Math.max(0, window.backend.categories.findIndex(x => x.id === data.categoryId) + 1);
        formCategory.editText = data.category || "";
        taskEditor.open();
        titleInput.forceActiveFocus();
    }
    function applyWeights() {
        let values = {};
        for (let i = 0; i < weightFields.count; i++) {
            const row = weightFields.itemAt(i) as WeightEntry;
            values[weightEditor.rows[i].id] = row.value;
        }
        window.backend.saveWeights(values);
    }
    function renameNode(data) {
        renameEditor.nodeId = data.id;
        renameInput.text = data.title;
        renameEditor.open();
        renameInput.forceActiveFocus();
    }

    Connections {
        target: window.backend
        function onStateChanged() {
            if (window.backend.busy)
                window.savedScrollY = detailScroll.contentItem.contentY;
            else if (window.closeWhenIdle) {
                window.closeWhenIdle = false;
                Qt.callLater(window.close);
            }
        }
        function onChanged() {
            const selected = window.backend.detail.id || "";
            if (window.lastTaskId !== selected) {
                window.lastTaskId = selected;
                detailScroll.contentItem.contentY = 0;
                window.savedScrollY = 0;
            }
        }
        function onFocusChanged() {
            if (window.activeFocus.recovery && focusRecordEditor.recoveryId !== window.activeFocus.recoveryId)
                focusRecordEditor.review();
        }
        function onActionSucceeded(action: string) {
            if (action !== "save" && action !== "refresh") {
                Qt.callLater(function () {
                    const view = detailScroll.contentItem;
                    view.contentY = Math.max(0, Math.min(window.savedScrollY, view.contentHeight - view.height));
                });
            }
            if (action === "focus_exit") {
                exitFocusDialog.close();
                window.close();
            }
            if (action === "focus" && focusEditor.visible)
                focusEditor.close();
            if (action === "focus_history" || (action === "focus" && !window.activeFocus.recovery))
                focusRecordEditor.close();
            if (action === "equal_weights")
                weightEditor.close();
            if (action === "save") {
                taskEditor.close();
                window.detailOpen = true;
                window.page = "tasks";
            }
            if (action === "delete")
                deleteEditor.close();
            if (action === "rename")
                renameEditor.close();
            if (action === "weights")
                weightEditor.close();
            if (action === "add_subtask")
                newSubtask.text = "";
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: window.narrow ? 16 : 24
        spacing: 16
        RowLayout {
            Layout.fillWidth: true
            Rectangle {
                implicitWidth: 44
                implicitHeight: 44
                radius: 14
                color: "#2b5949"
                Label {
                    anchors.centerIn: parent
                    text: "Р"
                    color: "white"
                    font.pixelSize: 26
                }
            }
            ColumnLayout {
                spacing: 2
                Label {
                    text: window.page === "tasks" ? qsTr("Мои задачи")
                        : window.page === "calendar" ? qsTr("Календарь") : qsTr("Статистика")
                    font.pixelSize: 25
                    font.weight: Font.DemiBold
                    color: "#243d33"
                }
                Caption {
                    text: qsTr("Большая работа начинается с маленького шага")
                }
            }
            Item {
                Layout.fillWidth: true
            }
            BusyIndicator {
                running: window.backend.busy
                visible: running
                implicitWidth: 30
                implicitHeight: 30
            }
            AccentButton {
                objectName: "newTaskButton"
                text: qsTr("+ Задача")
                enabled: !window.backend.busy
                onClicked: window.editTask({})
            }
        }
        RowLayout {
            Button {
                objectName: "tasksPageButton"
                text: qsTr("Задачи")
                highlighted: window.page === "tasks"
                onClicked: window.page = "tasks"
            }
            Button {
                objectName: "calendarPageButton"
                text: qsTr("Календарь")
                highlighted: window.page === "calendar"
                onClicked: window.page = "calendar"
            }
            Button {
                objectName: "statisticsPageButton"
                text: qsTr("Статистика")
                highlighted: window.page === "statistics"
                onClicked: window.page = "statistics"
            }
            Item {
                Layout.fillWidth: true
            }
        }
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: errorRow.implicitHeight + 20
            radius: 10
            color: "#fbe8dd"
            visible: window.backend.error.length > 0
            RowLayout {
                id: errorRow
                anchors.fill: parent
                anchors.margins: 10
                Label {
                    Layout.fillWidth: true
                    text: window.backend.error
                    wrapMode: Text.Wrap
                    color: "#853b22"
                }
                Button {
                    text: qsTr("Обновить")
                    enabled: !window.backend.busy
                    onClicked: window.backend.reload()
                }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            visible: !!window.activeFocus.elapsed
            spacing: 4
            RowLayout {
                Layout.fillWidth: true
                Label {
                    Layout.fillWidth: true
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    text: (window.activeFocus.recovery ? qsTr("Незавершённая сессия · ")
                        : window.activeFocus.paused ? qsTr("Пауза · ") : qsTr("Фокус · "))
                        + (window.activeFocus.title || "")
                }
                Label {
                    text: (window.activeFocus.elapsed || "") + (window.activeFocus.target
                        ? " / " + window.activeFocus.target : "")
                    font.weight: Font.DemiBold
                }
                Button {
                    visible: !window.activeFocus.recovery
                    objectName: "focusPauseResume"
                    text: window.activeFocus.paused ? qsTr("Продолжить") : qsTr("Пауза")
                    enabled: !window.backend.busy
                    onClicked: window.backend.changeFocus(window.activeFocus.paused ? "resume" : "pause")
                }
                Button {
                    visible: !window.activeFocus.recovery
                    objectName: "finishFocusButton"
                    text: qsTr("Завершить")
                    enabled: !window.backend.busy
                    onClicked: window.backend.changeFocus("finish")
                }
                Button {
                    objectName: "reviewFocusButton"
                    visible: !!window.activeFocus.recovery
                    enabled: !window.backend.busy
                    text: qsTr("Проверить окончание")
                    onClicked: focusRecordEditor.review()
                }
            }
            RowLayout {
                Layout.fillWidth: true
                visible: !!window.activeFocus.reached
                Caption {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: qsTr("Плановое время вышло. Можно завершить сессию или продолжить.")
                }
                Button {
                    objectName: "focusOvertimeButton"
                    text: qsTr("Продолжить сверх плана")
                    enabled: !window.backend.busy
                    onClicked: window.backend.changeFocus("overtime")
                }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            visible: window.page === "tasks" && (!window.narrow || !window.hasTask || !window.detailOpen)
            enabled: !window.backend.busy
            spacing: 8
            TextField {
                id: searchInput
                objectName: "searchInput"
                Layout.fillWidth: true
                placeholderText: qsTr("Поиск по названию, описанию и шагам…")
                onTextChanged: window.filterList()
            }
            RowLayout {
                Layout.fillWidth: true
                ComboBox {
                    id: statusFilter
                    objectName: "statusFilter"
                    Layout.preferredWidth: 155
                    model: [qsTr("Активные"), qsTr("Выполненные"), qsTr("Все задачи")]
                    onActivated: window.filterList()
                }
                ComboBox {
                    id: categoryFilter
                    objectName: "categoryFilter"
                    Layout.fillWidth: true
                    textRole: "title"
                    model: window.categoryOptions
                    currentIndex: Math.max(0, window.categoryOptions.findIndex(x => x.id === window.backend.categoryFilter))
                    onActivated: index => window.backend.setFilters(searchInput.text, ["active", "completed", "all"][statusFilter.currentIndex], window.categoryOptions[index].id)
                }
                Button {
                    objectName: "trashButton"
                    text: qsTr("Корзина") + " (" + window.backend.trash.length + ")"
                    enabled: !window.backend.busy
                    onClicked: trashEditor.open()
                }
            }
        }
        StatisticsPane {
            backend: window.backend
            visible: window.page === "statistics"
            Layout.fillWidth: true
            Layout.fillHeight: true
            onOpenTask: identifier => {
                window.backend.selectTask(identifier);
                window.detailOpen = true;
                window.page = "tasks";
            }
        }
        CalendarPane {
            id: calendarPane
            backend: window.backend
            visible: window.page === "calendar"
            Layout.fillWidth: true
            Layout.fillHeight: true
            onOpenTask: identifier => {
                window.backend.selectTask(identifier);
                window.detailOpen = true;
                window.page = "tasks";
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: window.page === "tasks"
            spacing: 18
            enabled: !window.backend.busy
            Rectangle {
                objectName: "listPanel"
                Layout.preferredWidth: 310
                Layout.fillWidth: window.narrow
                Layout.fillHeight: true
                visible: !window.narrow || !window.hasTask || !window.detailOpen
                color: "#eaece3"
                radius: 16
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 12
                    RowLayout {
                        Layout.fillWidth: true
                        Caption {
                            text: qsTr("ЗАДАЧИ")
                            font.letterSpacing: 1
                        }
                        Item {
                            Layout.fillWidth: true
                        }
                        Caption {
                            text: window.backend.tasks.length
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: window.backend.tasks.length === 0
                        text: qsTr("Задач не найдено.\nДобавьте задачу или измените фильтры.")
                        wrapMode: Text.Wrap
                        color: "#69766d"
                        padding: 10
                    }
                    ListView {
                        id: taskList
                        objectName: "taskList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 8
                        model: window.backend.tasks
                        ScrollBar.vertical: ScrollBar {}
                        delegate: ItemDelegate {
                            id: taskRow
                            required property var modelData
                            required property int index
                            objectName: "taskRow_" + index
                            width: ListView.view.width
                            height: taskRowContent.implicitHeight + 24
                            onClicked: {
                                window.backend.selectTask(modelData.id);
                                window.detailOpen = true;
                            }
                            background: Rectangle {
                                radius: 12
                                color: taskRow.modelData.overdue ? "#fff0e7" : taskRow.modelData.id === window.current.id ? "#ffffff" : taskRow.hovered ? "#f2f3ec" : "transparent"
                                border.color: taskRow.modelData.id === window.current.id ? "#bdcabb" : "transparent"
                            }
                            contentItem: ColumnLayout {
                                id: taskRowContent
                                spacing: 7
                                Caption {
                                    visible: taskRow.modelData.firstInGroup
                                    text: taskRow.modelData.group
                                    color: taskRow.modelData.overdue ? "#a34425" : "#69766d"
                                    font.weight: Font.DemiBold
                                }
                                Label {
                                    Layout.fillWidth: true
                                    textFormat: Text.PlainText
                                    text: taskRow.modelData.title
                                    font.pixelSize: 16
                                    font.weight: Font.Medium
                                    wrapMode: Text.Wrap
                                    color: "#243d33"
                                }
                                Caption {
                                    Layout.fillWidth: true
                                    text: (taskRow.modelData.overdue ? qsTr("Просрочено · ") : taskRow.modelData.deadline ? qsTr("До ") : "") + (taskRow.modelData.deadline || qsTr("Без дедлайна"))
                                    wrapMode: Text.Wrap
                                }
                                Caption {
                                    visible: !!taskRow.modelData.category
                                    textFormat: Text.PlainText
                                    text: taskRow.modelData.category
                                }
                                TaskProgress {
                                    Layout.fillWidth: true
                                    value: taskRow.modelData.progress
                                }
                                Caption {
                                    text: taskRow.modelData.completed ? qsTr("Выполнено") : taskRow.modelData.progressText + qsTr("% · В работе")
                                }
                                Button {
                                    objectName: "expandTask_" + taskRow.index
                                    visible: taskRow.modelData.subtasks.length > 0
                                    text: taskRow.modelData.expanded ? qsTr("Скрыть шаги") : qsTr("Показать шаги")
                                    onClicked: window.toggleTaskTree(taskRow.modelData.id)
                                }
                                Repeater {
                                    model: taskRow.modelData.expanded ? taskRow.modelData.subtasks : []
                                    delegate: ColumnLayout {
                                        id: listBranch
                                        required property var modelData
                                        Layout.fillWidth: true
                                        Label {
                                            Layout.fillWidth: true
                                            textFormat: Text.PlainText
                                            text: (listBranch.modelData.completed ? "✓ " : "○ ") + listBranch.modelData.title
                                            wrapMode: Text.Wrap
                                            color: "#48594e"
                                        }
                                        Repeater {
                                            model: listBranch.modelData.items
                                            delegate: Label {
                                                required property var modelData
                                                Layout.fillWidth: true
                                                Layout.leftMargin: 12
                                                textFormat: Text.PlainText
                                                text: (modelData.completed ? "✓ " : "○ ") + modelData.title
                                                wrapMode: Text.Wrap
                                                color: "#69766d"
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                objectName: "detailPanel"
                visible: !window.narrow || (window.hasTask && window.detailOpen)
                color: "#ffffff"
                radius: 18
                Label {
                    anchors.centerIn: parent
                    width: parent.width - 40
                    visible: !window.hasTask
                    text: qsTr("Что хочется сделать?\nСоздайте задачу, а затем разбейте её на шаги.")
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    color: "#69766d"
                    font.pixelSize: 18
                }
                ScrollView {
                    id: detailScroll
                    objectName: "detailScroll"
                    anchors.fill: parent
                    anchors.margins: 22
                    visible: window.hasTask
                    clip: true
                    contentWidth: availableWidth
                    ColumnLayout {
                        width: detailScroll.availableWidth
                        spacing: 16
                        RowLayout {
                            Layout.fillWidth: true
                            Button {
                                objectName: "backToListButton"
                                visible: window.narrow
                                text: qsTr("← К списку")
                                onClicked: window.detailOpen = false
                            }
                            Caption {
                                color: window.backend.selectedOverdue ? "#a34425" : "#69766d"
                                text: window.backend.selectedOverdue ? qsTr("ПРОСРОЧЕНО") : window.current.completed ? qsTr("ВЫПОЛНЕНО") : qsTr("В РАБОТЕ")
                                font.letterSpacing: 1
                            }
                            Item {
                                Layout.fillWidth: true
                            }
                            ToolButton {
                                objectName: "deleteTaskButton"
                                text: qsTr("Удалить")
                                onClicked: window.askDelete(window.current)
                            }
                            Button {
                                objectName: "editTaskButton"
                                text: qsTr("Изменить")
                                onClicked: window.editTask(window.current)
                            }
                        }
                        Caption {
                            Layout.fillWidth: true
                            visible: window.hasTask && !window.backend.tasks.some(row => row.id === window.current.id)
                            text: qsTr("Эта задача не входит в текущий фильтр списка")
                            wrapMode: Text.Wrap
                        }
                        Label {
                            Layout.fillWidth: true
                            textFormat: Text.PlainText
                            text: window.current.title || ""
                            font.pixelSize: 27
                            font.weight: Font.DemiBold
                            wrapMode: Text.Wrap
                            color: "#243d33"
                        }
                        Label {
                            Layout.fillWidth: true
                            textFormat: Text.PlainText
                            text: window.current.description || qsTr("Добавьте описание в карточку задачи")
                            wrapMode: Text.Wrap
                            color: window.current.description ? "#48594e" : "#849085"
                            font.pixelSize: 15
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: 12
                            Caption {
                                textFormat: Text.PlainText
                                visible: !!window.current.category
                                text: window.current.category || ""
                            }
                            Caption {
                                text: window.current.difficulty ? qsTr("Сложность: ") + window.current.difficulty + "/10" : qsTr("Сложность не задана")
                            }
                            Caption {
                                text: window.current.minutes ? qsTr("Ожидается: ") + window.current.minutes + qsTr(" мин") : qsTr("Время не задано")
                            }
                            Caption {
                                text: window.current.deadline ? qsTr("До ") + window.current.deadline : qsTr("Без дедлайна")
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Caption {
                                Layout.fillWidth: true
                                text: qsTr("Запланировано: ") + (window.current.plannedText || "0 мин")
                                wrapMode: Text.Wrap
                            }
                            Button {
                                objectName: "planTaskButton"
                                text: qsTr("+ Блок")
                                onClicked: {
                                    window.page = "calendar";
                                    calendarPane.beginCreate(window.current.id);
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Caption {
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                                text: qsTr("Затрачено: ") + (window.current.focusTotal || "00:00:00")
                            }
                            Button {
                                objectName: "startFocusButton"
                                enabled: !window.activeFocus.recovery
                                text: qsTr("Начать фокус")
                                onClicked: {
                                    focusEditor.taskId = window.current.id;
                                    focusEditor.replacing = !!window.activeFocus.elapsed;
                                    focusEditor.attempted = false;
                                    focusMinutes.text = "";
                                    focusEditor.open();
                                }
                            }
                        }
                        Button {
                            id: focusHistoryToggle
                            objectName: "focusHistoryButton"
                            checkable: true
                            text: qsTr("История фокуса") + " (" + (window.current.focusHistory || []).length + ")"
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: focusHistoryToggle.checked
                            Button {
                                objectName: "addFocusRecordButton"
                                text: qsTr("+ Запись вручную")
                                onClicked: focusRecordEditor.prepare("manual", {})
                            }
                            Caption {
                                visible: !(window.current.focusHistory || []).length
                                text: qsTr("Завершённых сессий пока нет")
                            }
                            Repeater {
                                model: focusHistoryToggle.checked ? window.current.focusHistory || [] : []
                                delegate: RowLayout {
                                    id: historyRow
                                    required property var modelData
                                    Layout.fillWidth: true
                                    Caption {
                                        Layout.fillWidth: true
                                        wrapMode: Text.Wrap
                                        text: historyRow.modelData.started + " · " + historyRow.modelData.elapsed
                                            + (historyRow.modelData.days ? "\n" + historyRow.modelData.days : "")
                                    }
                                    ToolButton {
                                        objectName: "editFocusRecord_" + historyRow.modelData.id
                                        text: qsTr("Исправить")
                                        onClicked: focusRecordEditor.prepare("edit", historyRow.modelData)
                                    }
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            TaskProgress {
                                Layout.fillWidth: true
                                from: 0
                                to: 100
                                value: window.current.progress || 0
                            }
                            Label {
                                text: (window.current.progressText || "0") + "%"
                                font.weight: Font.DemiBold
                                color: "#2b5949"
                            }
                        }
                        CheckBox {
                            objectName: "taskComplete"
                            text: qsTr("Задача выполнена")
                            checked: window.current.completed || false
                            nextCheckState: () => window.current.completed ? Qt.Checked : Qt.Unchecked
                            onClicked: window.backend.setCompleted(window.current.id, !window.current.completed)
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 1
                            color: "#e5e9e0"
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: qsTr("Подзадачи")
                                font.pixelSize: 19
                                font.weight: Font.DemiBold
                                color: "#243d33"
                            }
                            Item {
                                Layout.fillWidth: true
                            }
                            Button {
                                objectName: "weightsButton"
                                text: qsTr("Веса")
                                enabled: window.hasTask && window.current.subtasks.length > 0
                                onClicked: {
                                    weightEditor.rows = window.current.subtasks;
                                    weightEditor.open();
                                }
                            }
                        }
                        Caption {
                            Layout.fillWidth: true
                            text: qsTr("Вес шага засчитывается, когда выполнены все его пункты.")
                            wrapMode: Text.Wrap
                        }
                        Repeater {
                            model: window.current.subtasks || []
                            delegate: Rectangle {
                                id: branch
                                required property var modelData
                                required property int index
                                Layout.fillWidth: true
                                implicitHeight: branchContent.implicitHeight + 20
                                radius: 12
                                color: "#f5f6f0"
                                ColumnLayout {
                                    id: branchContent
                                    anchors.fill: parent
                                    anchors.margins: 10
                                    spacing: 6
                                    RowLayout {
                                        Layout.fillWidth: true
                                        CheckBox {
                                            objectName: "branchCheck_" + branch.index
                                            checked: branch.modelData.completed
                                            nextCheckState: () => branch.modelData.completed ? Qt.Checked : Qt.Unchecked
                                            onClicked: window.backend.setCompleted(branch.modelData.id, !branch.modelData.completed)
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            textFormat: Text.PlainText
                                            text: branch.modelData.title
                                            wrapMode: Text.Wrap
                                            color: "#243d33"
                                            font.weight: Font.Medium
                                        }
                                        Caption {
                                            text: branch.modelData.weight + "%"
                                        }
                                        ToolButton {
                                            text: "⋯"
                                            objectName: "renameBranch_" + branch.index
                                            Accessible.name: qsTr("Переименовать подзадачу")
                                            onClicked: window.renameNode(branch.modelData)
                                        }
                                        ToolButton {
                                            objectName: "deleteBranch_" + branch.index
                                            text: "×"
                                            Accessible.name: qsTr("Удалить подзадачу")
                                            onClicked: window.askDelete(branch.modelData)
                                        }
                                    }
                                    Repeater {
                                        model: branch.modelData.items
                                        delegate: RowLayout {
                                            id: leaf
                                            required property var modelData
                                            required property int index
                                            Layout.fillWidth: true
                                            Layout.leftMargin: 23
                                            CheckBox {
                                                objectName: "itemCheck_" + branch.index + "_" + leaf.index
                                                checked: leaf.modelData.completed
                                                nextCheckState: () => leaf.modelData.completed ? Qt.Checked : Qt.Unchecked
                                                onClicked: window.backend.setCompleted(leaf.modelData.id, !leaf.modelData.completed)
                                            }
                                            Label {
                                                Layout.fillWidth: true
                                                textFormat: Text.PlainText
                                                text: leaf.modelData.title
                                                wrapMode: Text.Wrap
                                                color: "#48594e"
                                            }
                                            ToolButton {
                                                text: "⋯"
                                                objectName: "renameItem_" + branch.index + "_" + leaf.index
                                                Accessible.name: qsTr("Переименовать пункт")
                                                onClicked: window.renameNode(leaf.modelData)
                                            }
                                            ToolButton {
                                                objectName: "deleteItem_" + branch.index + "_" + leaf.index
                                                text: "×"
                                                Accessible.name: qsTr("Удалить пункт")
                                                onClicked: window.askDelete(leaf.modelData)
                                            }
                                        }
                                    }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Layout.leftMargin: 23
                                        TextField {
                                            id: newItem
                                            objectName: "newItem_" + branch.index
                                            Layout.fillWidth: true
                                            placeholderText: qsTr("Новый пункт…")
                                            onAccepted: if (text.trim())
                                                window.backend.addItem(branch.modelData.id, text)
                                        }
                                        Button {
                                            objectName: "addItem_" + branch.index
                                            text: "+"
                                            Accessible.name: qsTr("Добавить пункт")
                                            enabled: newItem.text.trim().length > 0
                                            onClicked: window.backend.addItem(branch.modelData.id, newItem.text)
                                        }
                                    }
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            TextField {
                                id: newSubtask
                                objectName: "newSubtask"
                                Layout.fillWidth: true
                                placeholderText: qsTr("Название нового шага…")
                                onAccepted: if (text.trim())
                                    window.backend.addSubtask(text)
                            }
                            Button {
                                objectName: "addSubtaskButton"
                                text: qsTr("Добавить")
                                enabled: newSubtask.text.trim().length > 0
                                onClicked: window.backend.addSubtask(newSubtask.text)
                            }
                        }
                        Item {
                            implicitHeight: 10
                        }
                    }
                }
            }
        }
        Caption {
            Layout.fillWidth: true
            text: qsTr("В своём темпе. Шаг за шагом.")
            horizontalAlignment: Text.AlignHCenter
        }
    }

    EditorDialog {
        id: exitFocusDialog
        objectName: "exitFocusDialog"
        title: qsTr("Завершить фокус и выйти?")
        anchors.centerIn: parent
        width: Math.min(460, window.width - 32)
        modal: true
        closePolicy: window.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        contentItem: ColumnLayout {
            Caption {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("Текущее рабочее время сохранится в истории задачи.")
            }
            Label {
                Layout.fillWidth: true
                visible: window.backend.error.length > 0
                wrapMode: Text.Wrap
                text: window.backend.error
                color: "#853b22"
            }
        }
        footer: RowLayout {
            Item { Layout.fillWidth: true }
            Button {
                objectName: "cancelFocusExit"
                text: qsTr("Остаться")
                enabled: !window.backend.busy
                onClicked: exitFocusDialog.close()
            }
            AccentButton {
                objectName: "confirmFocusExit"
                text: qsTr("Завершить и выйти")
                enabled: !window.backend.busy
                onClicked: window.backend.finishFocusForExit()
            }
        }
    }

    EditorDialog {
        id: focusRecordEditor
        property string mode: "manual"
        property string taskId: ""
        property string recordId: ""
        property string recoveryId: ""
        property int revision: 0
        property bool attempted: false
        title: mode === "recover" ? qsTr("Проверить окончание сессии")
            : mode === "edit" ? qsTr("Исправить длительность") : qsTr("Запись времени вручную")
        anchors.centerIn: parent
        width: Math.min(540, window.width - 32)
        modal: true
        closePolicy: window.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape

        function prepare(kind, entry) {
            mode = kind;
            taskId = window.current.id || "";
            recordId = entry.id || "";
            revision = entry.revision || 0;
            recordStart.text = "";
            recordEnd.text = "";
            recordDuration.text = entry.durationInput || "";
            attempted = false;
            open();
        }
        function review() {
            recoveryId = window.activeFocus.recoveryId;
            prepare("recover", {});
        }
        contentItem: ColumnLayout {
            spacing: 8
            Caption {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: focusRecordEditor.mode === "edit"
                    ? qsTr("При сокращении убирается время с конца записи. При увеличении продлевается последний рабочий интервал.")
                    : focusRecordEditor.mode === "recover"
                        ? (window.activeFocus.lastStart
                            ? qsTr("Последний рабочий интервал начался: ") + window.activeFocus.lastStart
                            : qsTr("Сессия была на паузе. Уже записанное рабочее время сохранится."))
                        : qsTr("Укажи начало и окончание в местном времени.")
            }
            TextField {
                id: recordStart
                objectName: "focusRecordStart"
                visible: focusRecordEditor.mode === "manual"
                Layout.fillWidth: true
                placeholderText: qsTr("Начало · ДД.ММ.ГГГГ ЧЧ:ММ")
            }
            TextField {
                id: recordEnd
                objectName: "focusRecordEnd"
                visible: focusRecordEditor.mode !== "edit"
                Layout.fillWidth: true
                placeholderText: qsTr("Окончание · ДД.ММ.ГГГГ ЧЧ:ММ")
            }
            TextField {
                id: recordDuration
                objectName: "focusRecordDuration"
                visible: focusRecordEditor.mode === "edit"
                Layout.fillWidth: true
                placeholderText: qsTr("Длительность · ЧЧ:ММ:СС")
            }
            Button {
                objectName: "recoverKnownFocusButton"
                visible: focusRecordEditor.mode === "recover"
                enabled: !window.backend.busy
                text: qsTr("Завершить по последней отметке")
                onClicked: {
                    focusRecordEditor.attempted = true;
                    window.backend.recoverFocus(focusRecordEditor.recoveryId, "", true);
                }
            }
            Label {
                Layout.fillWidth: true
                visible: focusRecordEditor.attempted && window.backend.error.length > 0
                wrapMode: Text.Wrap
                text: window.backend.error
                color: "#853b22"
            }
        }
        footer: RowLayout {
            Item { Layout.fillWidth: true }
            Button {
                text: focusRecordEditor.mode === "recover" ? qsTr("Позже") : qsTr("Отмена")
                enabled: !window.backend.busy
                onClicked: focusRecordEditor.close()
            }
            AccentButton {
                objectName: "saveFocusRecordButton"
                text: qsTr("Сохранить")
                enabled: !window.backend.busy
                onClicked: {
                    focusRecordEditor.attempted = true;
                    if (focusRecordEditor.mode === "recover")
                        window.backend.recoverFocus(focusRecordEditor.recoveryId, recordEnd.text, false);
                    else if (focusRecordEditor.mode === "manual")
                        window.backend.addFocusRecord(focusRecordEditor.taskId, recordStart.text, recordEnd.text);
                    else
                        window.backend.editFocusRecord(focusRecordEditor.recordId, focusRecordEditor.revision, recordDuration.text);
                }
            }
        }
    }

    EditorDialog {
        id: focusEditor
        property string taskId: ""
        property bool replacing: false
        property bool attempted: false
        title: qsTr("Начать фокус-сессию")
        anchors.centerIn: parent
        width: Math.min(460, window.width - 32)
        modal: true
        closePolicy: window.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        contentItem: ColumnLayout {
            Caption {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("Оставь поле пустым для секундомера или укажи плановую длительность.")
            }
            TextField {
                id: focusMinutes
                objectName: "focusMinutesInput"
                Layout.fillWidth: true
                placeholderText: qsTr("Плановая длительность, мин")
                inputMethodHints: Qt.ImhDigitsOnly
            }
            Caption {
                Layout.fillWidth: true
                visible: focusEditor.replacing
                wrapMode: Text.Wrap
                text: qsTr("Текущая сессия завершится и сохранится в истории.")
            }
            Label {
                Layout.fillWidth: true
                visible: focusEditor.attempted && window.backend.error.length > 0
                wrapMode: Text.Wrap
                text: window.backend.error
                color: "#853b22"
            }
        }
        footer: RowLayout {
            Item { Layout.fillWidth: true }
            Button {
                text: qsTr("Отмена")
                enabled: !window.backend.busy
                onClicked: focusEditor.close()
            }
            AccentButton {
                objectName: "confirmFocusButton"
                enabled: !window.backend.busy
                text: focusEditor.replacing ? qsTr("Завершить текущую и начать") : qsTr("Начать")
                onClicked: {
                    focusEditor.attempted = true;
                    window.backend.startFocus(focusEditor.taskId, focusMinutes.text, focusEditor.replacing);
                }
            }
        }
    }

    EditorDialog {
        id: taskEditor
        property string taskId: ""
        title: taskId ? qsTr("Изменить задачу") : qsTr("Новая задача")
        anchors.centerIn: parent
        width: Math.min(550, window.width - 32)
        height: Math.min(680, window.height - 32)
        modal: true
        closePolicy: window.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        contentItem: ScrollView {
            id: formScroll
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                width: formScroll.availableWidth
                spacing: 12
                enabled: !window.backend.busy
                Caption {
                    text: qsTr("Название *")
                }
                TextField {
                    id: titleInput
                    objectName: "titleInput"
                    Layout.fillWidth: true
                    placeholderText: qsTr("Например, лабораторная по физике")
                }
                Caption {
                    text: qsTr("Категория · выберите или введите новую")
                }
                ComboBox {
                    id: formCategory
                    objectName: "formCategory"
                    Layout.fillWidth: true
                    editable: true
                    textRole: "title"
                    model: [
                        {
                            id: "",
                            title: ""
                        }
                    ].concat(window.backend.categories)
                }
                Caption {
                    text: qsTr("Описание")
                }
                TextArea {
                    id: descriptionInput
                    objectName: "descriptionInput"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 95
                    wrapMode: TextEdit.Wrap
                    background: Rectangle {
                        color: "#f5f6f0"
                        radius: 6
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    ColumnLayout {
                        Layout.fillWidth: true
                        Caption {
                            text: qsTr("Сложность · 1–10")
                        }
                        TextField {
                            id: difficultyInput
                            objectName: "difficultyInput"
                            Layout.fillWidth: true
                            placeholderText: qsTr("Не задана")
                            inputMethodHints: Qt.ImhDigitsOnly
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Caption {
                            text: qsTr("Время · минуты")
                        }
                        TextField {
                            id: minutesInput
                            objectName: "minutesInput"
                            Layout.fillWidth: true
                            placeholderText: qsTr("Не задано")
                            inputMethodHints: Qt.ImhFormattedNumbersOnly
                        }
                    }
                }
                Caption {
                    text: qsTr("Дедлайн · местное время")
                }
                TextField {
                    id: deadlineInput
                    objectName: "deadlineInput"
                    Layout.fillWidth: true
                    placeholderText: "ДД.ММ.ГГГГ ЧЧ:ММ"
                }
                Caption {
                    Layout.fillWidth: true
                    text: qsTr("Оставьте поле пустым, если срока нет. Обязательно только название.")
                    wrapMode: Text.Wrap
                }
                Label {
                    Layout.fillWidth: true
                    visible: window.backend.error.length > 0
                    text: window.backend.error
                    color: "#a34425"
                    wrapMode: Text.Wrap
                }
            }
        }
        footer: RowLayout {
            spacing: 10
            Item {
                Layout.fillWidth: true
            }
            Button {
                text: qsTr("Отмена")
                enabled: !window.backend.busy
                onClicked: taskEditor.close()
            }
            AccentButton {
                objectName: "saveTaskButton"
                text: qsTr("Сохранить")
                enabled: !window.backend.busy
                onClicked: window.backend.saveTask(taskEditor.taskId, {
                    title: titleInput.text,
                    description: descriptionInput.text,
                    difficulty: difficultyInput.text,
                    minutes: minutesInput.text,
                    deadline: deadlineInput.text,
                    category: formCategory.editText,
                    categoryId: formCategory.currentIndex > 0 ? window.backend.categories[formCategory.currentIndex - 1].id : ""
                })
            }
        }
    }
    EditorDialog {
        id: renameEditor
        property string nodeId: ""
        title: qsTr("Название пункта")
        anchors.centerIn: parent
        width: Math.min(460, window.width - 40)
        modal: true
        closePolicy: window.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        contentItem: ColumnLayout {
            TextField {
                id: renameInput
                objectName: "renameInput"
                Layout.fillWidth: true
                enabled: !window.backend.busy
            }
            Label {
                Layout.fillWidth: true
                visible: window.backend.error.length > 0
                text: window.backend.error
                color: "#a34425"
                wrapMode: Text.Wrap
            }
        }
        footer: RowLayout {
            Item {
                Layout.fillWidth: true
            }
            Button {
                text: qsTr("Отмена")
                enabled: !window.backend.busy
                onClicked: renameEditor.close()
            }
            AccentButton {
                objectName: "saveRenameButton"
                text: qsTr("Сохранить")
                enabled: !window.backend.busy
                onClicked: window.backend.renameNode(renameEditor.nodeId, renameInput.text)
            }
        }
    }
    EditorDialog {
        id: weightEditor
        property var rows: []
        title: qsTr("Вес подзадач")
        anchors.centerIn: parent
        width: Math.min(490, window.width - 40)
        height: Math.min(520, window.height - 40)
        modal: true
        closePolicy: window.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        contentItem: ScrollView {
            id: weightScroll
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                width: weightScroll.availableWidth
                spacing: 12
                enabled: !window.backend.busy
                Caption {
                    Layout.fillWidth: true
                    text: qsTr("Укажите доли с точностью до 0,01%. Сумма — 100%.")
                    wrapMode: Text.Wrap
                }
                Repeater {
                    id: weightFields
                    model: weightEditor.rows
                    delegate: WeightEntry {}
                }
                Caption {
                    Layout.fillWidth: true
                    text: qsTr("Пункт с нулевым весом тоже нужно выполнить, чтобы завершить задачу.")
                    wrapMode: Text.Wrap
                }
                Label {
                    Layout.fillWidth: true
                    visible: window.backend.error.length > 0
                    text: window.backend.error
                    color: "#a34425"
                    wrapMode: Text.Wrap
                }
                Button {
                    text: qsTr("Распределить поровну")
                    onClicked: window.backend.equalWeights()
                }
            }
        }
        footer: RowLayout {
            Item {
                Layout.fillWidth: true
            }
            Button {
                text: qsTr("Отмена")
                enabled: !window.backend.busy
                onClicked: weightEditor.close()
            }
            AccentButton {
                objectName: "saveWeightsButton"
                text: qsTr("Сохранить")
                enabled: !window.backend.busy
                onClicked: window.applyWeights()
            }
        }
    }
    EditorDialog {
        id: deleteEditor
        property string nodeId: ""
        property string nodeTitle: ""
        title: qsTr("Переместить в корзину?")
        anchors.centerIn: parent
        width: Math.min(460, window.width - 32)
        modal: true
        closePolicy: window.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        contentItem: ColumnLayout {
            Label {
                Layout.fillWidth: true
                textFormat: Text.PlainText
                text: deleteEditor.nodeTitle
                wrapMode: Text.Wrap
                font.bold: true
            }
            Caption {
                Layout.fillWidth: true
                text: qsTr("Пункт и его вложенные шаги исчезнут из списка. Их можно будет восстановить из корзины.")
                wrapMode: Text.Wrap
            }
            Label {
                Layout.fillWidth: true
                visible: !!window.backend.error
                text: window.backend.error
                color: "#a34425"
                wrapMode: Text.Wrap
            }
        }
        footer: RowLayout {
            Item {
                Layout.fillWidth: true
            }
            Button {
                objectName: "cancelDeleteButton"
                text: qsTr("Отмена")
                enabled: !window.backend.busy
                onClicked: deleteEditor.close()
            }
            AccentButton {
                objectName: "confirmDeleteButton"
                text: qsTr("В корзину")
                enabled: !window.backend.busy
                onClicked: window.backend.deleteNode(deleteEditor.nodeId)
            }
        }
    }
    EditorDialog {
        id: trashEditor
        title: qsTr("Корзина")
        anchors.centerIn: parent
        width: Math.min(600, window.width - 32)
        height: Math.min(560, window.height - 32)
        modal: true
        closePolicy: window.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        contentItem: ScrollView {
            id: trashScroll
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                width: trashScroll.availableWidth
                spacing: 12
                Caption {
                    Layout.fillWidth: true
                    text: qsTr("Восстановите родительскую задачу перед её отдельно удалёнными пунктами. Если состав или веса менялись, восстановленный шаг получит новую долю.")
                    wrapMode: Text.Wrap
                }
                Label {
                    visible: window.backend.trash.length === 0
                    text: qsTr("Корзина пуста")
                }
                Repeater {
                    model: window.backend.trash
                    delegate: RowLayout {
                        id: trashRow
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                textFormat: Text.PlainText
                                text: trashRow.modelData.title
                                wrapMode: Text.Wrap
                            }
                            Caption {
                                text: [qsTr("Задача"), qsTr("Подзадача"), qsTr("Пункт")][trashRow.modelData.kind] + " · " + trashRow.modelData.deletedText
                            }
                        }
                        Button {
                            objectName: "restore_" + trashRow.index
                            text: qsTr("Восстановить")
                            enabled: !window.backend.busy
                            onClicked: window.backend.restoreEntry(trashRow.modelData.id)
                        }
                    }
                }
                Label {
                    Layout.fillWidth: true
                    visible: !!window.backend.error
                    text: window.backend.error
                    color: "#a34425"
                    wrapMode: Text.Wrap
                }
            }
        }
        footer: Button {
            objectName: "closeTrashButton"
            text: qsTr("Закрыть")
            enabled: !window.backend.busy
            onClicked: trashEditor.close()
        }
    }
}
