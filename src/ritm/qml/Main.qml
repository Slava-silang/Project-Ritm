pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

ApplicationWindow {
    id: window
    property var desktop: null
    required property var backend
    property alias appearance: uiAppearance
    Appearance {
        id: uiAppearance
        objectName: "uiAppearance"
        themeKey: window.backend.appearance.theme
        scale: window.backend.appearance.scale / 100
    }
    objectName: "mainWindow"
    width: 1120
    height: 780
    minimumWidth: 640
    minimumHeight: 480
    visible: true
    title: qsTr("Ритм — задачи")
    color: window.appearance.colors.background
    font.family: "Sans Serif"
    font.pixelSize: window.appearance.dp(14)
    palette.highlight: window.appearance.colors.accentFill
    palette.button: window.appearance.colors.raised
    palette.buttonText: window.appearance.colors.text
    palette.brightText: window.appearance.colors.text
    palette.text: window.appearance.colors.text
    palette.windowText: window.appearance.colors.text
    palette.accent: window.appearance.colors.accent
    palette.highlightedText: window.appearance.colors.onAccent
    palette.light: window.appearance.colors.raised
    palette.mid: window.appearance.colors.border
    palette.dark: window.appearance.colors.border
    palette.shadow: window.appearance.colors.background
    palette.base: window.appearance.colors.surface
    palette.window: window.appearance.colors.surface
    palette.placeholderText: window.appearance.colors.muted
    property var current: window.backend.detail
    property var activeFocus: window.backend.focus
    property bool hasTask: !!current.id
    property bool narrow: width < window.appearance.dp(850)
    property bool detailOpen: true
    property string page: "tasks"
    property bool closeWhenIdle: false

    property bool exiting: false
    function requestExit() {
        exiting = true;
        window.close();
    }
    onClosing: event => {
        if (window.desktop && !window.exiting && window.desktop.trayAvailable) {
            event.accepted = false;
            window.hide();
            return;
        }
        if (window.backend.busy) {
            event.accepted = false;
            window.closeWhenIdle = true;
        } else if (window.activeFocus.elapsed && !window.activeFocus.recovery) {
            event.accepted = false;
            exitFocusDialog.open();
        } else if (window.desktop) {
            window.desktop.exitNow();
        }
    }

    property var categoryOptions: [
        {
            id: "",
            title: qsTr("Все категории")
        },
        {
            id: "none",
            title: qsTr("Без категории")
        }
    ].concat(window.backend.categories)

    property bool modalOpen: [
        systemDialog, appearanceDialog, backupDialog, restoreBackupDialog, attachmentDialog,
        exitFocusDialog, focusRecordEditor, focusEditor, taskEditor, renameEditor,
        weightEditor, deleteEditor, trashEditor, purgeEditor, attachmentPicker,
        backupFolderPicker, backupFilePicker
    ].some(dialog => dialog.visible)
    property bool shortcutsEnabled: !window.backend.busy && !window.modalOpen

    function openAppearance() {
        appearanceTheme.currentIndex = appearanceDialog.themeKeys.indexOf(window.backend.appearance.theme);
        appearanceScale.currentIndex = appearanceDialog.scales.indexOf(window.backend.appearance.scale);
        appearanceDialog.attempted = false;
        appearanceDialog.open();
    }
    function scaleStep(direction) {
        const steps = [100, 125, 150];
        const index = Math.max(0, steps.indexOf(window.backend.appearance.scale));
        const next = Math.max(0, Math.min(steps.length - 1, index + direction));
        window.backend.saveAppearance(window.backend.appearance.theme, steps[next]);
    }

    component PageShortcut: Shortcut {
        required property string destination
        enabled: window.shortcutsEnabled
        onActivated: {
            window.page = destination;
            window.detailOpen = true;
        }
    }
    PageShortcut { sequence: "Ctrl+1"; destination: "tasks" }
    PageShortcut { sequence: "Ctrl+2"; destination: "calendar" }
    PageShortcut { sequence: "Ctrl+3"; destination: "statistics" }
    PageShortcut { sequence: "Ctrl+4"; destination: "materials" }
    Shortcut {
        sequences: [StandardKey.New]
        enabled: window.shortcutsEnabled
        onActivated: window.editTask({})
    }
    Shortcut {
        sequences: [StandardKey.Find]
        enabled: window.shortcutsEnabled
        onActivated: {
            window.page = "tasks";
            window.detailOpen = false;
            searchInput.forceActiveFocus();
            searchInput.selectAll();
        }
    }
    Shortcut {
        sequence: "Ctrl+,"
        enabled: window.shortcutsEnabled
        onActivated: window.openAppearance()
    }
    Shortcut {
        sequences: ["Ctrl++", "Ctrl+="]
        enabled: window.shortcutsEnabled
        onActivated: window.scaleStep(1)
    }
    Shortcut {
        sequence: "Ctrl+-"
        enabled: window.shortcutsEnabled
        onActivated: window.scaleStep(-1)
    }
    Shortcut {
        sequence: "Ctrl+0"
        enabled: window.shortcutsEnabled
        onActivated: window.backend.saveAppearance(window.backend.appearance.theme, 100)
    }

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
    function showAttachments(data) {
        attachmentDialog.taskId = window.current.id;
        attachmentDialog.nodeId = data.id;
        attachmentDialog.nodeTitle = data.title;
        attachmentDialog.attempted = false;
        attachmentDialog.choosingMaterial = false;
        attachmentDialog.open();
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
        horizontalPadding: window.appearance.dp(18)
        verticalPadding: window.appearance.dp(11)
        contentItem: Label {
            text: actionButton.text
            color: window.appearance.colors.onAccent
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: window.appearance.dp(9)
            color: !actionButton.enabled ? window.appearance.colors.disabled : actionButton.down ? window.appearance.colors.accentDown : actionButton.hovered ? window.appearance.colors.accentHover : window.appearance.colors.accentFill
            border.width: actionButton.visualFocus ? 2 : 0
            border.color: window.appearance.colors.border
        }
    }
    component TaskProgress: ProgressBar {
        id: taskProgress
        from: 0
        to: 100
        background: null
        contentItem: Item {
            implicitHeight: window.appearance.dp(6)
            Rectangle {
                anchors.fill: parent
                radius: window.appearance.dp(3)
                color: window.appearance.colors.track
            }
            Rectangle {
                width: parent.width * taskProgress.position
                height: parent.height
                radius: window.appearance.dp(3)
                color: window.appearance.colors.accent
            }
        }
    }
    component EditorDialog: Dialog {
        padding: window.appearance.dp(16)
        background: Rectangle {
            color: window.appearance.colors.surface
            radius: window.appearance.dp(14)
            border.color: window.appearance.colors.border
        }
    }
    component Caption: Label {
        color: window.appearance.colors.muted
        font.pixelSize: window.appearance.dp(13)
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
            Layout.preferredWidth: window.appearance.dp(85)
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
            if (action === "appearance")
                appearanceDialog.close();
            if (action === "inspect_backup")
                restoreBackupDialog.open();
            if (action === "restore_backup") {
                restoreBackupDialog.close();
                window.detailOpen = false;
                window.page = "tasks";
            }
            if (action === "link_material")
                attachmentDialog.choosingMaterial = false;
            if (action === "focus_exit") {
                exitFocusDialog.close();
                window.requestExit();
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
            if (action === "purge_trash") {
                purgeEditor.close();
                trashEditor.selection = [];
            }
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
        anchors.margins: window.appearance.dp(window.narrow ? 16 : 24)
        spacing: window.appearance.dp(16)
        RowLayout {
            Layout.fillWidth: true
            Rectangle {
                implicitWidth: window.appearance.dp(44)
                implicitHeight: window.appearance.dp(44)
                radius: window.appearance.dp(14)
                color: window.appearance.colors.accentFill
                Label {
                    anchors.centerIn: parent
                    text: "Р"
                    color: window.appearance.colors.onAccent
                    font.pixelSize: window.appearance.dp(26)
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: window.appearance.dp(2)
                Label {
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    text: window.page === "tasks" ? qsTr("Мои задачи")
                        : window.page === "calendar" ? qsTr("Календарь")
                        : window.page === "statistics" ? qsTr("Статистика") : qsTr("Материалы")
                    font.pixelSize: window.appearance.dp(25)
                    font.weight: Font.DemiBold
                    color: window.appearance.colors.text
                }
                Caption {
                    visible: !window.narrow
                    text: qsTr("Большая работа начинается с маленького шага")
                }
            }
            Item {
                Layout.fillWidth: true
            }
            BusyIndicator {
                running: window.backend.busy
                visible: running
                implicitWidth: window.appearance.dp(30)
                implicitHeight: window.appearance.dp(30)
            }
            AccentButton {
                objectName: "newTaskButton"
                text: qsTr("+ Задача")
                enabled: !window.backend.busy
                onClicked: window.editTask({})
            }
        }
        Flow {
            Layout.fillWidth: true
            spacing: window.appearance.dp(4)
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
            Button {
                objectName: "materialsPageButton"
                text: qsTr("Материалы")
                highlighted: window.page === "materials"
                onClicked: window.page = "materials"
            }
            Button {
                objectName: "backupsButton"
                text: qsTr("Копии")
                enabled: !window.backend.busy
                onClicked: {
                    backupDialog.attempted = false;
                    backupDialog.open();
                }
            }
            Button {
                objectName: "systemButton"
                text: qsTr("Приложение")
                visible: !!window.desktop
                onClicked: {
                    autoStart.checked = window.desktop.settings.enabled;
                    hiddenStart.checked = window.desktop.settings.hidden;
                    systemDialog.open();
                }
            }
            Button {
                objectName: "appearanceButton"
                text: qsTr("Вид")
                enabled: !window.backend.busy
                onClicked: window.openAppearance()
            }
        }
        Caption {
            Layout.fillWidth: true
            visible: !!window.desktop && !window.desktop.trayAvailable
            wrapMode: Text.Wrap
            text: qsTr("Трей недоступен: крестик завершает приложение. Настройки запуска — в меню «Приложение».")
        }
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: errorRow.implicitHeight + window.appearance.dp(20)
            radius: window.appearance.dp(10)
            color: window.appearance.colors.warningSurface
            visible: window.backend.error.length > 0
            RowLayout {
                id: errorRow
                anchors.fill: parent
                anchors.margins: window.appearance.dp(10)
                Label {
                    Layout.fillWidth: true
                    text: window.backend.error
                    wrapMode: Text.Wrap
                    color: window.appearance.colors.warning
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
            spacing: window.appearance.dp(4)
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
            spacing: window.appearance.dp(8)
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
                    Layout.preferredWidth: window.appearance.dp(155)
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
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: window.page === "materials"
            RowLayout {
                Layout.fillWidth: true
                Caption {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: qsTr("Книги и другие файлы, которые можно использовать в нескольких задачах.")
                }
                AccentButton {
                    objectName: "addMaterialButton"
                    text: qsTr("+ Материал")
                    enabled: !window.backend.busy
                    onClicked: {
                        attachmentPicker.forLibrary = true;
                        attachmentPicker.open();
                    }
                }
            }
            Caption {
                visible: window.backend.materials.length === 0
                text: qsTr("Материалов пока нет")
            }
            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: window.appearance.dp(6)
                model: window.backend.materials
                ScrollBar.vertical: ScrollBar {}
                delegate: RowLayout {
                    id: materialRow
                    required property var modelData
                    width: ListView.view.width - window.appearance.dp(12)
                    Label {
                        Layout.fillWidth: true
                        textFormat: Text.PlainText
                        text: materialRow.modelData.name
                        elide: Text.ElideMiddle
                    }
                    Button {
                        objectName: "openMaterial_" + materialRow.modelData.id
                        text: qsTr("Открыть")
                        enabled: !window.backend.busy
                        onClicked: window.backend.openAttachment(materialRow.modelData.id)
                    }
                }
            }
        }
        StatisticsPane {
            appearance: window.appearance
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
            appearance: window.appearance
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
            spacing: window.appearance.dp(18)
            enabled: !window.backend.busy
            Rectangle {
                objectName: "listPanel"
                Layout.preferredWidth: window.appearance.dp(310)
                Layout.fillWidth: window.narrow
                Layout.fillHeight: true
                visible: !window.narrow || !window.hasTask || !window.detailOpen
                color: window.appearance.colors.panel
                radius: window.appearance.dp(16)
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: window.appearance.dp(12)
                    spacing: window.appearance.dp(12)
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
                        color: window.appearance.colors.muted
                        padding: window.appearance.dp(10)
                    }
                    ListView {
                        id: taskList
                        objectName: "taskList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: window.appearance.dp(8)
                        model: window.backend.tasks
                        ScrollBar.vertical: ScrollBar {}
                        delegate: ItemDelegate {
                            id: taskRow
                            required property var modelData
                            required property int index
                            objectName: "taskRow_" + index
                            width: ListView.view.width
                            height: taskRowContent.implicitHeight + window.appearance.dp(24)
                            onClicked: {
                                window.backend.selectTask(modelData.id);
                                window.detailOpen = true;
                            }
                            background: Rectangle {
                                radius: window.appearance.dp(12)
                                color: taskRow.modelData.overdue ? window.appearance.colors.warningSurface : taskRow.modelData.id === window.current.id ? window.appearance.colors.surface : taskRow.hovered ? window.appearance.colors.raised : "transparent"
                                border.color: taskRow.modelData.id === window.current.id ? window.appearance.colors.border : "transparent"
                            }
                            contentItem: ColumnLayout {
                                id: taskRowContent
                                spacing: window.appearance.dp(7)
                                Caption {
                                    visible: taskRow.modelData.firstInGroup
                                    text: taskRow.modelData.group
                                    color: taskRow.modelData.overdue ? window.appearance.colors.warning : window.appearance.colors.muted
                                    font.weight: Font.DemiBold
                                }
                                Label {
                                    Layout.fillWidth: true
                                    textFormat: Text.PlainText
                                    text: taskRow.modelData.title
                                    font.pixelSize: window.appearance.dp(16)
                                    font.weight: Font.Medium
                                    wrapMode: Text.Wrap
                                    color: window.appearance.colors.text
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
                                            color: window.appearance.colors.secondary
                                        }
                                        Repeater {
                                            model: listBranch.modelData.items
                                            delegate: Label {
                                                required property var modelData
                                                Layout.fillWidth: true
                                                Layout.leftMargin: window.appearance.dp(12)
                                                textFormat: Text.PlainText
                                                text: (modelData.completed ? "✓ " : "○ ") + modelData.title
                                                wrapMode: Text.Wrap
                                                color: window.appearance.colors.muted
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
                color: window.appearance.colors.surface
                radius: window.appearance.dp(18)
                Label {
                    anchors.centerIn: parent
                    width: parent.width - window.appearance.dp(40)
                    visible: !window.hasTask
                    text: qsTr("Что хочется сделать?\nСоздайте задачу, а затем разбейте её на шаги.")
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    color: window.appearance.colors.muted
                    font.pixelSize: window.appearance.dp(18)
                }
                ScrollView {
                    id: detailScroll
                    objectName: "detailScroll"
                    anchors.fill: parent
                    anchors.margins: window.appearance.dp(22)
                    visible: window.hasTask
                    clip: true
                    contentWidth: availableWidth
                    ColumnLayout {
                        width: detailScroll.availableWidth
                        spacing: window.appearance.dp(16)
                        RowLayout {
                            Layout.fillWidth: true
                            Button {
                                objectName: "backToListButton"
                                visible: window.narrow
                                text: qsTr("← К списку")
                                onClicked: window.detailOpen = false
                            }
                            Caption {
                                color: window.backend.selectedOverdue ? window.appearance.colors.warning : window.appearance.colors.muted
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
                            font.pixelSize: window.appearance.dp(27)
                            font.weight: Font.DemiBold
                            wrapMode: Text.Wrap
                            color: window.appearance.colors.text
                        }
                        Button {
                            objectName: "taskAttachments"
                            text: qsTr("Вложения (%1)").arg((window.backend.attachments[window.current.id] || []).length)
                            onClicked: window.showAttachments(window.current)
                        }
                        Label {
                            Layout.fillWidth: true
                            textFormat: Text.PlainText
                            text: window.current.description || qsTr("Добавьте описание в карточку задачи")
                            wrapMode: Text.Wrap
                            color: window.current.description ? window.appearance.colors.secondary : window.appearance.colors.muted
                            font.pixelSize: window.appearance.dp(15)
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: window.appearance.dp(12)
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
                                color: window.appearance.colors.accent
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
                            implicitHeight: window.appearance.dp(1)
                            color: window.appearance.colors.track
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: qsTr("Подзадачи")
                                font.pixelSize: window.appearance.dp(19)
                                font.weight: Font.DemiBold
                                color: window.appearance.colors.text
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
                                implicitHeight: branchContent.implicitHeight + window.appearance.dp(20)
                                radius: window.appearance.dp(12)
                                color: window.appearance.colors.raised
                                ColumnLayout {
                                    id: branchContent
                                    anchors.fill: parent
                                    anchors.margins: window.appearance.dp(10)
                                    spacing: window.appearance.dp(6)
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
                                            color: window.appearance.colors.text
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
                                            objectName: "branchAttachments_" + branch.index
                                            text: qsTr("Файлы")
                                            Accessible.name: qsTr("Вложения подзадачи")
                                            onClicked: window.showAttachments(branch.modelData)
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
                                            Layout.leftMargin: window.appearance.dp(23)
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
                                                color: window.appearance.colors.secondary
                                            }
                                            ToolButton {
                                                text: "⋯"
                                                objectName: "renameItem_" + branch.index + "_" + leaf.index
                                                Accessible.name: qsTr("Переименовать пункт")
                                                onClicked: window.renameNode(leaf.modelData)
                                            }
                                            ToolButton {
                                                objectName: "itemAttachments_" + branch.index + "_" + leaf.index
                                                text: qsTr("Файлы")
                                                Accessible.name: qsTr("Вложения пункта")
                                                onClicked: window.showAttachments(leaf.modelData)
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
                                        Layout.leftMargin: window.appearance.dp(23)
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
                            implicitHeight: window.appearance.dp(10)
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
        id: appearanceDialog
        property var themeKeys: ["space", "jungle", "light", "dark", "system"]
        property var scales: [100, 125, 150]
        property bool attempted: false
        title: qsTr("Оформление")
        anchors.centerIn: parent
        width: Math.min(window.appearance.dp(480), window.width - 32)
        height: Math.min(window.appearance.dp(480), window.height - 32)
        modal: true
        closePolicy: window.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        contentItem: ScrollView {
            id: appearanceScroll
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                width: appearanceScroll.availableWidth
                spacing: window.appearance.dp(12)
                Caption { text: qsTr("Тема") }
                ComboBox {
                    id: appearanceTheme
                    objectName: "appearanceTheme"
                    Layout.fillWidth: true
                    model: [qsTr("Космос"), qsTr("Джунгли"), qsTr("Светлая"), qsTr("Тёмная"), qsTr("Как в системе")]
                }
                Caption {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: qsTr("Пока все темы используют временное оформление. Финальный вид появится после общего дизайна.")
                }
                Caption { text: qsTr("Масштаб интерфейса") }
                ComboBox {
                    id: appearanceScale
                    objectName: "appearanceScale"
                    Layout.fillWidth: true
                    model: ["100%", "125%", "150%"]
                }
                Caption {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: qsTr("Ctrl+N — новая задача, Ctrl+F — поиск, Ctrl+1…4 — разделы. Ctrl++/− — масштаб, Ctrl+0 — 100%. Tab — переход между элементами.")
                }
                Label {
                    Layout.fillWidth: true
                    visible: !!window.backend.appearanceError || (appearanceDialog.attempted && !!window.backend.error)
                    text: window.backend.appearanceError || window.backend.error
                    color: window.appearance.colors.warning
                    wrapMode: Text.Wrap
                }
            }
        }
        footer: RowLayout {
            Item { Layout.fillWidth: true }
            Button {
                objectName: "cancelAppearance"
                text: qsTr("Отмена")
                enabled: !window.backend.busy
                onClicked: appearanceDialog.close()
            }
            AccentButton {
                objectName: "saveAppearance"
                text: qsTr("Применить")
                enabled: !window.backend.busy
                onClicked: {
                    appearanceDialog.attempted = true;
                    window.backend.saveAppearance(
                        appearanceDialog.themeKeys[appearanceTheme.currentIndex],
                        appearanceDialog.scales[appearanceScale.currentIndex]);
                }
            }
        }
    }

    FolderDialog {
        id: backupFolderPicker
        title: qsTr("Папка резервных копий")
        onAccepted: {
            backupDialog.attempted = true;
            window.backend.setBackupFolder(selectedFolder);
        }
    }
    FileDialog {
        id: backupFilePicker
        objectName: "backupFilePicker"
        title: qsTr("Выбрать резервную копию")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("Архивы Ритма (*.zip)")]
        onAccepted: {
            backupDialog.attempted = true;
            window.backend.inspectBackup(selectedFile);
        }
    }

    EditorDialog {
        id: backupDialog
        property bool attempted: false
        title: qsTr("Резервные копии")
        anchors.centerIn: parent
        width: Math.min(window.appearance.dp(550), window.width - 32)
        modal: true
        closePolicy: window.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        contentItem: ColumnLayout {
            Caption {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("Ритм создаёт копию при первом запуске за день и хранит семь последних ежедневных копий.")
            }
            RowLayout {
                Layout.fillWidth: true
                Label {
                    Layout.fillWidth: true
                    textFormat: Text.PlainText
                    text: window.backend.backups.folder || ""
                    wrapMode: Text.WrapAnywhere
                }
                Button {
                    text: qsTr("Выбрать папку")
                    enabled: !window.backend.busy
                    onClicked: backupFolderPicker.open()
                }
            }
            Caption {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                visible: !!window.backend.backups.saved
                text: qsTr("Создана копия: %1").arg(window.backend.backups.saved || "")
            }
            Caption {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                visible: !!window.backend.backups.restored
                text: qsTr("Профиль восстановлен из резервной копии")
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                visible: text.length > 0
                color: window.appearance.colors.warning
                text: backupDialog.attempted && window.backend.error
                    ? window.backend.error : (window.backend.backups.automaticError || "")
            }
        }
        footer: RowLayout {
            Item { Layout.fillWidth: true }
            Button {
                objectName: "closeBackups"
                text: qsTr("Закрыть")
                enabled: !window.backend.busy
                onClicked: backupDialog.close()
            }
            Button {
                objectName: "chooseBackupArchive"
                text: qsTr("Восстановить…")
                enabled: !window.backend.busy
                onClicked: {
                    backupFilePicker.currentFolder = window.backend.backupFolderUrl;
                    backupFilePicker.open();
                }
            }
            AccentButton {
                objectName: "createBackupButton"
                text: qsTr("Создать копию")
                enabled: !window.backend.busy
                onClicked: {
                    backupDialog.attempted = true;
                    window.backend.createBackup();
                }
            }
        }
    }

    EditorDialog {
        id: restoreBackupDialog
        title: qsTr("Восстановить из копии?")
        anchors.centerIn: parent
        width: Math.min(window.appearance.dp(460), window.width - 32)
        modal: true
        closePolicy: window.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        contentItem: ColumnLayout {
            Label {
                objectName: "backupDate"
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("Дата копии: %1").arg((window.backend.backups.preview || {}).dateText || "")
                font.bold: true
            }
            Caption {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("Данные из копии заменят текущие задачи, расписание, историю фокуса, вложения и материалы.")
            }
            Label {
                Layout.fillWidth: true
                visible: !!window.backend.error
                text: window.backend.error
                wrapMode: Text.Wrap
                color: window.appearance.colors.warning
            }
        }
        footer: RowLayout {
            Item { Layout.fillWidth: true }
            Button {
                objectName: "cancelBackupRestore"
                text: qsTr("Отмена")
                enabled: !window.backend.busy
                onClicked: restoreBackupDialog.close()
            }
            AccentButton {
                objectName: "confirmBackupRestore"
                text: qsTr("Заменить данные")
                enabled: !window.backend.busy && !!(window.backend.backups.preview || {}).token
                onClicked: window.backend.restoreBackup()
            }
        }
    }

    FileDialog {
        id: attachmentPicker
        objectName: "attachmentPicker"
        property bool forLibrary: false
        title: forLibrary ? qsTr("Выбрать файл для материалов") : qsTr("Выбрать файл для вложения")
        fileMode: FileDialog.OpenFile
        onAccepted: {
            if (forLibrary) {
                window.backend.addMaterial(selectedFile);
            } else {
                attachmentDialog.attempted = true;
                window.backend.attachFile(attachmentDialog.taskId, attachmentDialog.nodeId, selectedFile);
            }
        }
    }

    EditorDialog {
        id: attachmentDialog
        property string taskId: ""
        property string nodeId: ""
        property string nodeTitle: ""
        property bool attempted: false
        property bool choosingMaterial: false
        property var files: window.backend.attachments[nodeId] || []
        property var availableMaterials: window.backend.materials.filter(
            material => !attachmentDialog.files.some(file => file.id === material.id)
        )
        title: qsTr("Вложения")
        anchors.centerIn: parent
        width: Math.min(window.appearance.dp(540), window.width - 32)
        height: Math.min(window.appearance.dp(400), window.height - 32)
        modal: true
        closePolicy: window.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        contentItem: ColumnLayout {
            Label {
                Layout.fillWidth: true
                text: attachmentDialog.nodeTitle
                textFormat: Text.PlainText
                elide: Text.ElideRight
                font.weight: Font.DemiBold
            }
            Caption {
                visible: attachmentDialog.files.length === 0
                text: qsTr("Вложений пока нет")
            }
            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: window.appearance.dp(6)
                model: attachmentDialog.files
                ScrollBar.vertical: ScrollBar {}
                delegate: RowLayout {
                    id: attachmentRow
                    required property var modelData
                    width: ListView.view.width - window.appearance.dp(12)
                    Label {
                        Layout.fillWidth: true
                        text: attachmentRow.modelData.name
                        textFormat: Text.PlainText
                        elide: Text.ElideMiddle
                    }
                    Caption {
                        visible: attachmentRow.modelData.shared
                        text: qsTr("Из материалов")
                    }
                    Button {
                        objectName: "openAttachment_" + attachmentRow.modelData.id
                        text: qsTr("Открыть")
                        enabled: !window.backend.busy
                        onClicked: {
                            attachmentDialog.attempted = true;
                            window.backend.openAttachment(attachmentRow.modelData.id);
                        }
                    }
                }
            }
            Caption {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                visible: attachmentDialog.choosingMaterial && !attachmentDialog.availableMaterials.length
                text: window.backend.materials.length
                    ? qsTr("Все материалы уже прикреплены")
                    : qsTr("Сначала добавь файл во вкладке «Материалы»")
            }
            RowLayout {
                Layout.fillWidth: true
                visible: attachmentDialog.choosingMaterial && attachmentDialog.availableMaterials.length > 0
                ComboBox {
                    id: materialChoice
                    objectName: "materialChoice"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    model: attachmentDialog.availableMaterials
                    textRole: "name"
                    valueRole: "id"
                }
                Button {
                    objectName: "linkMaterialButton"
                    text: qsTr("Прикрепить")
                    enabled: !window.backend.busy && materialChoice.currentIndex >= 0
                    onClicked: {
                        attachmentDialog.attempted = true;
                        window.backend.linkMaterial(attachmentDialog.taskId, attachmentDialog.nodeId, materialChoice.currentValue);
                    }
                }
            }
            Label {
                Layout.fillWidth: true
                visible: attachmentDialog.attempted && window.backend.error.length > 0
                text: window.backend.error
                wrapMode: Text.Wrap
                color: window.appearance.colors.warning
            }
        }
        footer: RowLayout {
            Item { Layout.fillWidth: true }
            Button {
                objectName: "closeAttachments"
                text: qsTr("Закрыть")
                enabled: !window.backend.busy
                onClicked: attachmentDialog.close()
            }
            Button {
                objectName: "chooseFromMaterials"
                text: qsTr("Из материалов")
                enabled: !window.backend.busy
                onClicked: attachmentDialog.choosingMaterial = !attachmentDialog.choosingMaterial
            }
            AccentButton {
                objectName: "chooseAttachment"
                text: qsTr("+ Файл")
                enabled: !window.backend.busy
                onClicked: {
                    attachmentPicker.forLibrary = false;
                    attachmentPicker.open();
                }
            }
        }
    }

    EditorDialog {
        id: systemDialog
        objectName: "systemDialog"
        title: qsTr("Запуск и завершение")
        anchors.centerIn: parent
        width: Math.min(window.appearance.dp(480), window.width - 32)
        height: Math.min(window.appearance.dp(480), window.height - 32)
        modal: true
        contentItem: ScrollView {
            id: systemScroll
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                width: systemScroll.availableWidth
                Caption {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: window.desktop && window.desktop.trayAvailable
                        ? qsTr("Крестик скрывает окно в трей. Фокусировка продолжает работать. Открыть окно и выйти можно через значок Ритма.")
                        : qsTr("Системный трей недоступен. Крестик завершает приложение; активную сессию потребуется подтвердить.")
                }
                CheckBox {
                    id: autoStart
                    objectName: "autoStart"
                    text: qsTr("Запускать при входе в систему")
                    enabled: !!window.desktop && window.desktop.settings.supported && !window.desktop.settings.busy
                }
                CheckBox {
                    id: hiddenStart
                    objectName: "hiddenStart"
                    text: qsTr("Запускать скрытым в трей")
                    enabled: autoStart.enabled && autoStart.checked
                }
                Caption {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: qsTr("Без доступного трея окно всегда показывается. Настройка компаньона появится при его подключении.")
                }
                Caption {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: window.desktop ? window.desktop.settings.error : ""
                    color: window.appearance.colors.warning
                }
            }
        }
        footer: ColumnLayout {
            Button {
                objectName: "saveAutostart"
                text: qsTr("Применить автозапуск")
                enabled: autoStart.enabled
                onClicked: window.desktop.save(autoStart.checked, hiddenStart.checked)
            }
            Button {
                objectName: "exitApplication"
                text: qsTr("Выйти из приложения")
                onClicked: {
                    systemDialog.close();
                    window.requestExit();
                }
            }
            Button {
                objectName: "closeSystemDialog"
                text: qsTr("Закрыть настройки")
                onClicked: systemDialog.close()
            }
        }
    }

    EditorDialog {
        id: exitFocusDialog
        objectName: "exitFocusDialog"
        onClosed: if (!window.backend.busy) window.exiting = false
        title: qsTr("Завершить фокус и выйти?")
        anchors.centerIn: parent
        width: Math.min(window.appearance.dp(460), window.width - 32)
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
                color: window.appearance.colors.warning
            }
        }
        footer: RowLayout {
            Item { Layout.fillWidth: true }
            Button {
                objectName: "cancelFocusExit"
                text: qsTr("Остаться")
                enabled: !window.backend.busy
                onClicked: { window.exiting = false; exitFocusDialog.close(); }
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
        width: Math.min(window.appearance.dp(540), window.width - 32)
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
            spacing: window.appearance.dp(8)
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
                color: window.appearance.colors.warning
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
        width: Math.min(window.appearance.dp(460), window.width - 32)
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
                color: window.appearance.colors.warning
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
        width: Math.min(window.appearance.dp(550), window.width - 32)
        height: Math.min(window.appearance.dp(680), window.height - 32)
        modal: true
        closePolicy: window.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        contentItem: ScrollView {
            id: formScroll
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                width: formScroll.availableWidth
                spacing: window.appearance.dp(12)
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
                    Layout.preferredHeight: window.appearance.dp(95)
                    wrapMode: TextEdit.Wrap
                    background: Rectangle {
                        color: window.appearance.colors.raised
                        radius: window.appearance.dp(6)
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
                    color: window.appearance.colors.warning
                    wrapMode: Text.Wrap
                }
            }
        }
        footer: RowLayout {
            spacing: window.appearance.dp(10)
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
        width: Math.min(window.appearance.dp(460), window.width - 40)
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
                color: window.appearance.colors.warning
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
        width: Math.min(window.appearance.dp(490), window.width - 40)
        height: Math.min(window.appearance.dp(520), window.height - 40)
        modal: true
        closePolicy: window.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        contentItem: ScrollView {
            id: weightScroll
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                width: weightScroll.availableWidth
                spacing: window.appearance.dp(12)
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
                    color: window.appearance.colors.warning
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
        width: Math.min(window.appearance.dp(460), window.width - 32)
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
                color: window.appearance.colors.warning
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
        property var selection: []
        property var selectedRows: window.backend.trash.filter(row => trashEditor.selection.includes(row.id))
        onOpened: selection = []
        title: qsTr("Корзина")
        anchors.centerIn: parent
        width: Math.min(window.appearance.dp(600), window.width - 32)
        height: Math.min(window.appearance.dp(560), window.height - 32)
        modal: true
        closePolicy: window.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        contentItem: ScrollView {
            id: trashScroll
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                width: trashScroll.availableWidth
                spacing: window.appearance.dp(12)
                Caption {
                    Layout.fillWidth: true
                    text: qsTr("Восстановите родительскую задачу перед её отдельно удалёнными пунктами. Если состав или веса менялись, восстановленный шаг получит новую долю.")
                    wrapMode: Text.Wrap
                }
                Label {
                    visible: window.backend.trash.length === 0
                    text: qsTr("Корзина пуста")
                }
                Caption {
                    Layout.fillWidth: true
                    visible: window.backend.pendingCleanup > 0
                    wrapMode: Text.Wrap
                    text: qsTr("Осталось удалить копий файлов: %1. Повтори очистку, когда файлы будут доступны.")
                        .arg(window.backend.pendingCleanup)
                }
                Button {
                    visible: window.backend.pendingCleanup > 0
                    enabled: !window.backend.busy
                    text: qsTr("Повторить очистку файлов")
                    onClicked: window.backend.reload()
                }
                Repeater {
                    model: window.backend.trash
                    delegate: RowLayout {
                        id: trashRow
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true
                        CheckBox {
                            objectName: "selectTrash_" + trashRow.index
                            Accessible.name: qsTr("Выбрать: %1").arg(trashRow.modelData.title)
                            checked: trashEditor.selection.includes(trashRow.modelData.id)
                            enabled: !window.backend.busy
                            nextCheckState: () => checked ? Qt.Unchecked : Qt.Checked
                            onClicked: {
                                const id = trashRow.modelData.id;
                                if (trashEditor.selection.includes(id))
                                    trashEditor.selection = trashEditor.selection.filter(value => value !== id);
                                else
                                    trashEditor.selection = trashEditor.selection.concat([id]);
                            }
                        }
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
                    color: window.appearance.colors.warning
                    wrapMode: Text.Wrap
                }
            }
        }
        footer: RowLayout {
            Item { Layout.fillWidth: true }
            Button {
                objectName: "closeTrashButton"
                text: qsTr("Закрыть")
                enabled: !window.backend.busy
                onClicked: trashEditor.close()
            }
            Button {
                objectName: "purgeSelectedButton"
                text: qsTr("Удалить выбранное (%1)").arg(trashEditor.selectedRows.length)
                enabled: !window.backend.busy && trashEditor.selectedRows.length > 0
                onClicked: {
                    purgeEditor.entries = trashEditor.selectedRows;
                    purgeEditor.attempted = false;
                    purgeEditor.open();
                }
            }
        }
    }
    EditorDialog {
        id: purgeEditor
        property var entries: []
        property bool attempted: false
        title: qsTr("Удалить без восстановления?")
        anchors.centerIn: parent
        width: Math.min(window.appearance.dp(500), window.width - 32)
        height: Math.min(window.appearance.dp(370), window.height - 32)
        modal: true
        closePolicy: window.backend.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        contentItem: ColumnLayout {
            Caption {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("Выбранные записи и их связанные данные будут удалены. Исходные файлы и общая библиотека сохранятся.")
            }
            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: purgeEditor.entries
                delegate: Label {
                    required property var modelData
                    width: ListView.view.width
                    text: modelData.title
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                }
            }
            Label {
                Layout.fillWidth: true
                visible: purgeEditor.attempted && !!window.backend.error
                text: window.backend.error
                color: window.appearance.colors.warning
                wrapMode: Text.Wrap
            }
        }
        footer: RowLayout {
            Item { Layout.fillWidth: true }
            Button {
                objectName: "cancelPurgeButton"
                text: qsTr("Отмена")
                enabled: !window.backend.busy
                onClicked: purgeEditor.close()
            }
            AccentButton {
                objectName: "confirmPurgeButton"
                text: qsTr("Удалить навсегда")
                enabled: !window.backend.busy
                onClicked: {
                    purgeEditor.attempted = true;
                    window.backend.purgeTrash(purgeEditor.entries.map(entry => entry.id));
                }
            }
        }
    }

}
