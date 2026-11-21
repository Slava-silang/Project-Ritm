pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    objectName: "mailProposalPane"
    required property var helper
    required property var appearance

    component UiButton: RitmButton { appearance: pane.appearance }
    component UiField: RitmTextField { appearance: pane.appearance }
    required property var sourceState
    property var proposalState: helper ? helper.state : ({busy: false})
    property var form: ({})
    property bool editing: false
    property int selectedIndex: -1
    onSourceStateChanged: {
        if (selectedIndex !== sourceState.selected) {
            selectedIndex = sourceState.selected;
            if (prompt) prompt.text = "";
        }
    }
    function number(text) { return text.trim() ? (/^\d+$/.test(text.trim()) ? Number(text) : text) : null; }
    function fields() { return form.task.task_id ? form.task.changes : form.task; }
    function startEdit() {
        form = JSON.parse(JSON.stringify(proposalState.payload));
        if (form.kind === "task") {
            if (form.task.task_id && !form.task.changes) form.task.changes = {};
            const values = fields();
            values.title = proposalState.title;
            values.description = proposalState.description;
            values.difficulty = proposalState.difficulty;
            values.expected_minutes = proposalState.minutes;
            if (!form.task.subtasks) form.task.subtasks = [];
        }
        editing = true;
    }
    Connections {
        target: pane.helper
        function onChanged() {
            sendConsent.checked = false;
            if (!pane.proposalState.hasDraft) {
                pane.editing = false;
                pane.form = {};
            }
            if (!pane.proposalState.busy && !pane.proposalState.error) pane.editing = false;
        }
    }
    Label {
        Layout.fillWidth: true
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        text: qsTr("Получатель: ") + (pane.proposalState.recipient || "")
    }
    Label {
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        text: qsTr("Будут переданы тема, отправитель, дата и текст выбранного письма, ваш запрос и названия, описания, сроки, состояние и подзадачи всех активных задач. Для обновления — связанная задача (включая выполненную) или событие. Вложения и история чата не передаются. Проверьте получателя и его условия обработки данных.")
    }
    TextArea {
        id: prompt
        objectName: "mailPrompt"
        Layout.fillWidth: true
        placeholderText: qsTr("Что предложить из выбранного письма? Уточните сроки или нужные правки")
        wrapMode: TextEdit.Wrap
        enabled: !pane.proposalState.busy
        onTextChanged: sendConsent.checked = false
    }
    CheckBox {
        id: sendConsent
        objectName: "mailSendConsent"
        Layout.fillWidth: true
        enabled: !pane.proposalState.busy && !!pane.proposalState.supported && !!pane.sourceState.bodyAvailable
        text: qsTr("Разрешаю обработать эти данные выбранной моделью для этого запроса")
        contentItem: Label { text: sendConsent.text; wrapMode: Text.Wrap; leftPadding: sendConsent.indicator.width + sendConsent.spacing }
    }
    UiButton {
        objectName: "mailGenerate"
        text: qsTr("Подготовить предложение")
        enabled: sendConsent.enabled && sendConsent.checked
        onClicked: pane.helper.generate(prompt.text, true)
    }
    Label {
        Layout.fillWidth: true
        visible: !!pane.proposalState.busy
        wrapMode: Text.Wrap
        text: pane.proposalState.saving ? qsTr("Сохраняем подтверждённое предложение…") : qsTr("Готовим предложение…")
    }
    Label {
        Layout.fillWidth: true
        objectName: "mailAnswer"
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        visible: !!pane.proposalState.answer
        text: pane.proposalState.answer || ""
    }
    Label {
        Layout.fillWidth: true
        objectName: "mailError"
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        visible: !!pane.proposalState.error
        text: pane.proposalState.error || ""
    }
    Label {
        objectName: "mailPreview"
        Layout.fillWidth: true
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        visible: !!pane.proposalState.hasDraft
        text: pane.proposalState.preview || ""
    }
    UiButton {
        objectName: "mailEdit"
        text: qsTr("Исправить предложение")
        visible: !!pane.proposalState.hasDraft
        enabled: !pane.proposalState.busy
        onClicked: pane.startEdit()
    }
    ColumnLayout {
        Layout.fillWidth: true
        visible: pane.editing
        enabled: !pane.proposalState.busy
        Label { text: qsTr("Название") }
        UiField {
            objectName: "mailEditTitle"
            Layout.fillWidth: true
            placeholderText: qsTr("Название")
            text: pane.form.kind === "task" ? (pane.fields().title || "") : (pane.form.event ? pane.form.event.title : "")
            onTextEdited: { if (pane.form.kind === "task") pane.fields().title = text; else pane.form.event.title = text; }
        }
        Label { text: qsTr("Описание"); visible: pane.form.kind === "task" }
        TextArea {
            objectName: "mailEditDescription"
            Layout.fillWidth: true
            visible: pane.form.kind === "task"
            placeholderText: qsTr("Описание")
            text: pane.form.kind === "task" ? pane.fields().description || "" : ""
            wrapMode: TextEdit.Wrap
            onTextChanged: if (pane.editing && activeFocus && pane.form.kind === "task") pane.fields().description = text
        }
        Label { text: qsTr("Сложность (1–10)"); visible: pane.form.kind === "task" }
        UiField {
            objectName: "mailEditDifficulty"
            Layout.fillWidth: true
            visible: pane.form.kind === "task"
            placeholderText: qsTr("Сложность 1–10, можно оставить пустой")
            text: pane.proposalState.difficulty === null ? "" : String(pane.proposalState.difficulty)
            onTextEdited: pane.fields().difficulty = pane.number(text)
        }
        Label { text: qsTr("Ожидаемое время, минуты"); visible: pane.form.kind === "task" }
        UiField {
            objectName: "mailEditMinutes"
            Layout.fillWidth: true
            visible: pane.form.kind === "task"
            placeholderText: qsTr("Ожидаемое время, минуты")
            text: pane.proposalState.minutes === null ? "" : String(pane.proposalState.minutes)
            onTextEdited: pane.fields().expected_minutes = pane.number(text)
        }
        Label { text: qsTr("Дедлайн, необязательно"); visible: pane.form.kind === "task" }
        DateTimeField {
            id: deadline
            objectName: "mailEditDeadline"
            Layout.fillWidth: true
            appearance: pane.appearance
            visible: pane.form.kind === "task"
            allowEmpty: true
            allowOffset: true
            text: pane.proposalState.deadline || ""
            placeholderText: qsTr("Дедлайн, необязательно")
        }
        Label { text: qsTr("Начало события"); visible: pane.form.kind === "event" }
        DateTimeField {
            id: start
            objectName: "mailEditStart"
            Layout.fillWidth: true
            appearance: pane.appearance
            visible: pane.form.kind === "event"
            allowOffset: true
            text: pane.proposalState.start || ""
            placeholderText: qsTr("Начало события")
        }
        Label { text: qsTr("Окончание события"); visible: pane.form.kind === "event" }
        DateTimeField {
            id: end
            objectName: "mailEditEnd"
            Layout.fillWidth: true
            appearance: pane.appearance
            visible: pane.form.kind === "event"
            allowOffset: true
            text: pane.proposalState.end || ""
            placeholderText: qsTr("Окончание события")
        }
        CheckBox {
            visible: pane.form.kind === "task" && !!pane.form.task && !!pane.form.task.changes
                && pane.form.task.changes.completed !== undefined
            text: qsTr("Выполнение главной задачи")
            checked: !!(pane.form.task && pane.form.task.changes && pane.form.task.changes.completed)
            onToggled: {
                if (pane.form.kind === "task" && pane.form.task && pane.form.task.changes
                        && pane.form.task.changes.completed !== undefined)
                    pane.form.task.changes.completed = checked;
            }
        }
        Repeater {
            model: pane.form.kind === "task" && pane.form.task ? pane.form.task.nodes || [] : []
            delegate: RowLayout {
                id: patch
                required property var modelData
                required property int index
                Layout.fillWidth: true
                UiField {
                    Layout.fillWidth: true
                    visible: patch.modelData.title !== undefined
                    text: patch.modelData.title || ""
                    onTextEdited: pane.form.task.nodes[patch.index].title = text
                }
                CheckBox {
                    visible: patch.modelData.completed !== undefined
                    checked: !!patch.modelData.completed
                    text: qsTr("Выполнено")
                    onToggled: pane.form.task.nodes[patch.index].completed = checked
                }
            }
        }
        Repeater {
            model: pane.form.kind === "task" && pane.form.task ? pane.form.task.subtasks || [] : []
            delegate: ColumnLayout {
                id: branch
                required property var modelData
                required property int index
                Layout.fillWidth: true
                UiField {
                    objectName: "mailEditBranch_" + branch.index
                    Layout.fillWidth: true
                    text: branch.modelData.title
                    onTextEdited: pane.form.task.subtasks[branch.index].title = text
                }
                Repeater {
                    model: branch.modelData.items || []
                    delegate: UiField {
                        required property string modelData
                        required property int index
                        objectName: "mailEditLeaf_" + branch.index + "_" + index
                        Layout.fillWidth: true
                        text: modelData
                        onTextEdited: pane.form.task.subtasks[branch.index].items[index] = text
                    }
                }
            }
        }
        UiField {
            objectName: "mailEditWeights"
            Layout.fillWidth: true
            visible: pane.form.kind === "task" && !!pane.form.task && !!pane.form.task.weights && pane.form.task.weights.length > 0
            placeholderText: qsTr("Веса подзадач через пробел")
            text: pane.form.task && pane.form.task.weights ? pane.form.task.weights.join(" ") : ""
            onTextEdited: pane.form.task.weights = text.trim().split(/\s+/)
        }
        UiButton {
            objectName: "mailCheckEdits"
            text: qsTr("Проверить правки и обновить предпросмотр")
            onClicked: pane.helper.edit(JSON.stringify(pane.form), deadline.text, start.text, end.text)
        }
    }
    UiButton {
        objectName: "mailApply"
        text: qsTr("Подтвердить и сохранить предложение")
        visible: !!pane.proposalState.hasDraft
        enabled: !pane.proposalState.busy && !pane.editing
        onClicked: pane.helper.apply(true)
    }
    UiButton {
        objectName: "mailCancelProposal"
        text: qsTr("Отменить предложение")
        visible: !!pane.proposalState.hasDraft || !!pane.proposalState.busy
        enabled: !pane.proposalState.saving
        onClicked: { pane.helper.cancel(); pane.editing = false; }
    }
}
