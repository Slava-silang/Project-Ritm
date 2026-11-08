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
    property var current: window.backend.detail
    property bool hasTask: !!current.id
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
        }
        function onChanged() {
            const selected = window.backend.detail.id || "";
            if (window.lastTaskId !== selected) {
                window.lastTaskId = selected;
                detailScroll.contentItem.contentY = 0;
                window.savedScrollY = 0;
            }
        }
        function onActionSucceeded(action: string) {
            if (action !== "save" && action !== "refresh") {
                Qt.callLater(function () {
                    const view = detailScroll.contentItem;
                    view.contentY = Math.max(0, Math.min(window.savedScrollY, view.contentHeight - view.height));
                });
            }
            if (action === "equal_weights")
                weightEditor.close();
            if (action === "save")
                taskEditor.close();
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
        anchors.margins: 24
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
                    text: qsTr("Мои задачи")
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
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 18
            enabled: !window.backend.busy
            Rectangle {
                Layout.preferredWidth: window.width < 850 ? 200 : 300
                Layout.fillHeight: true
                color: "#eaece3"
                radius: 16
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 12
                    RowLayout {
                        Layout.fillWidth: true
                        Caption {
                            text: qsTr("ВСЕ ЗАДАЧИ")
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
                        text: qsTr("Пока здесь пусто.\nДобавьте первую задачу.")
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
                            width: ListView.view.width
                            height: taskRowContent.implicitHeight + 26
                            onClicked: window.backend.selectTask(modelData.id)
                            background: Rectangle {
                                radius: 12
                                color: taskRow.modelData.id === window.current.id ? "#ffffff" : taskRow.hovered ? "#f2f3ec" : "transparent"
                                border.color: taskRow.modelData.id === window.current.id ? "#bdcabb" : "transparent"
                            }
                            contentItem: ColumnLayout {
                                id: taskRowContent
                                spacing: 7
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
                                    text: taskRow.modelData.deadline ? qsTr("До ") + taskRow.modelData.deadline : qsTr("Без дедлайна")
                                    wrapMode: Text.Wrap
                                }
                                TaskProgress {
                                    Layout.fillWidth: true
                                    from: 0
                                    to: 100
                                    value: taskRow.modelData.progress
                                }
                                Caption {
                                    text: taskRow.modelData.completed ? qsTr("Выполнено") : taskRow.modelData.progressText + qsTr("% · В работе")
                                }
                            }
                        }
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
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
                            Caption {
                                text: window.current.completed ? qsTr("ВЫПОЛНЕНО") : qsTr("В РАБОТЕ")
                                font.letterSpacing: 1
                            }
                            Item {
                                Layout.fillWidth: true
                            }
                            Button {
                                objectName: "editTaskButton"
                                text: qsTr("Изменить")
                                onClicked: window.editTask(window.current)
                            }
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
                                text: window.current.difficulty ? qsTr("Сложность: ") + window.current.difficulty + "/10" : qsTr("Сложность не задана")
                            }
                            Caption {
                                text: window.current.minutes ? window.current.minutes + qsTr(" мин") : qsTr("Время не задано")
                            }
                            Caption {
                                text: window.current.deadline ? qsTr("До ") + window.current.deadline : qsTr("Без дедлайна")
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
                    deadline: deadlineInput.text
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
}
