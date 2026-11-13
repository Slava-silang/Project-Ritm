pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "assistantDialog"
    required property var agent
    required property var taskBackend
    required property var uiAppearance
    property var state: agent ? agent.state : ({busy: false, hasDraft: false, saving: false})
    property var form: ({})
    property bool dirty: false
    property bool editing: false
    title: qsTr("Тестовый помощник — без сети")
    modal: false
    closePolicy: state.saving ? Popup.NoAutoClose : Popup.CloseOnEscape
    onClosed: if (agent) agent.cancel()
    background: Rectangle {
        color: dialog.uiAppearance.colors.surface
        border.color: dialog.uiAppearance.colors.border
        radius: dialog.uiAppearance.dp(14)
    }
    function loadDraft() {
        form = JSON.parse(JSON.stringify(state.draft || {}));
        titleInput.text = form.title || "";
        descriptionInput.text = form.description || "";
        difficultyInput.text = form.difficulty ? String(form.difficulty) : "";
        minutesInput.text = form.expected_minutes ? String(form.expected_minutes) : "";
        deadlineInput.text = state.deadlineText || "";
        weightsInput.text = (form.weights || []).join("; ");
        dirty = false;
        editing = true;
    }
    function checkDraft() {
        let proposal = JSON.parse(JSON.stringify(form));
        if (!state.split) {
            proposal.title = titleInput.text;
            proposal.description = descriptionInput.text;
            proposal.difficulty = difficultyInput.text.trim() || null;
            proposal.expected_minutes = minutesInput.text.trim() || null;
        }
        proposal.weights = weightsInput.text.trim() ? weightsInput.text.split(";").map(x => x.trim().replace(",", ".")) : null;
        agent.edit(JSON.stringify(proposal), deadlineInput.text);
        if (!agent.state.error) {
            form = JSON.parse(JSON.stringify(agent.state.draft));
            weightsInput.text = form.weights.join("; ");
            dirty = false;
            editing = false;
        }
    }
    Connections {
        target: dialog.agent
        function onChanged() {
            if (!dialog.state.hasDraft)
                dialog.editing = false;
        }
        function onApplied(identifier) {
            dialog.taskBackend.reload();
            dialog.close();
        }
    }
    contentItem: ScrollView {
        id: scroll
        clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: scroll.availableWidth
            spacing: dialog.uiAppearance.dp(10)
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("Сейчас используется локальный шаблон для проверки черновиков. Запросы никуда не отправляются. Вход в аккаунт и настоящий чат появятся на следующих этапах.")
            }
            ComboBox {
                id: operation
                objectName: "assistantOperation"
                Layout.fillWidth: true
                enabled: !dialog.state.busy && !dialog.state.hasDraft
                model: [qsTr("Создать новую задачу"), qsTr("Добавить подзадачи к выбранной задаче")]
            }
            Label {
                Layout.fillWidth: true
                visible: operation.currentIndex === 1
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
                text: qsTr("Выбрано: ") + (dialog.taskBackend.detail.title || qsTr("сначала выберите задачу в списке"))
            }
            TextArea {
                id: prompt
                objectName: "assistantPrompt"
                Layout.fillWidth: true
                placeholderText: qsTr("Опишите работу")
                wrapMode: TextEdit.Wrap
                enabled: !dialog.state.busy
            }
            Button {
                objectName: "assistantGenerate"
                text: dialog.state.busy ? qsTr("Подготовка…") : qsTr("Сформировать тестовый черновик")
                enabled: !dialog.state.busy && !dialog.state.hasDraft && !dialog.taskBackend.busy
                    && (operation.currentIndex === 0 || !!dialog.taskBackend.detail.id)
                onClicked: dialog.agent.generate(prompt.text, operation.currentIndex === 1 ? dialog.taskBackend.detail.id : "")
            }
            Label {
                Layout.fillWidth: true
                visible: !!dialog.state.error
                text: dialog.state.error || ""
                textFormat: Text.PlainText
                color: dialog.uiAppearance.colors.warning
                wrapMode: Text.Wrap
            }
            Label {
                Layout.fillWidth: true
                visible: dialog.state.hasDraft && !dialog.editing
                text: dialog.state.preview || ""
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
            }
            ColumnLayout {
                Layout.fillWidth: true
                visible: dialog.state.hasDraft && dialog.editing
                enabled: !dialog.state.busy
                Label { text: qsTr("Название"); visible: !dialog.state.split }
                TextField {
                    id: titleInput
                    objectName: "draftTitle"
                    Layout.fillWidth: true
                    visible: !dialog.state.split
                    onTextEdited: dialog.dirty = true
                }
                Label { text: qsTr("Описание"); visible: !dialog.state.split }
                TextArea {
                    id: descriptionInput
                    Layout.fillWidth: true
                    visible: !dialog.state.split
                    wrapMode: TextEdit.Wrap
                    onTextChanged: if (activeFocus) dialog.dirty = true
                }
                Label { text: qsTr("Сложность 1–10 (необязательно)"); visible: !dialog.state.split }
                TextField {
                    id: difficultyInput
                    objectName: "draftDifficulty"
                    Layout.fillWidth: true
                    visible: !dialog.state.split
                    onTextEdited: dialog.dirty = true
                }
                Label { text: qsTr("Ожидаемое время, минуты"); visible: !dialog.state.split }
                TextField {
                    id: minutesInput
                    objectName: "draftMinutes"
                    Layout.fillWidth: true
                    visible: !dialog.state.split
                    onTextEdited: dialog.dirty = true
                }
                Label { text: qsTr("Дедлайн: ДД.ММ.ГГГГ ЧЧ:ММ или пусто"); visible: !dialog.state.split }
                TextField {
                    id: deadlineInput
                    objectName: "draftDeadline"
                    Layout.fillWidth: true
                    visible: !dialog.state.split
                    onTextEdited: dialog.dirty = true
                }
                Label {
                    Layout.fillWidth: true
                    text: qsTr("Веса всех подзадач первого уровня (%) через точку с запятой. Пусто — автоматическое распределение.")
                    wrapMode: Text.Wrap
                }
                TextField {
                    id: weightsInput
                    objectName: "draftWeights"
                    Layout.fillWidth: true
                    onTextEdited: dialog.dirty = true
                }
                Repeater {
                    model: dialog.form.subtasks || []
                    delegate: ColumnLayout {
                        id: branch
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true
                        Label { text: qsTr("Новая подзадача ") + (branch.index + 1) }
                        TextField {
                            objectName: "draftSubtask_" + branch.index
                            Layout.fillWidth: true
                            text: branch.modelData.title
                            onTextEdited: { dialog.form.subtasks[branch.index].title = text; dialog.dirty = true; }
                        }
                        Label { text: qsTr("Пункты второго уровня, каждый с новой строки") }
                        TextArea {
                            objectName: "draftItems_" + branch.index
                            Layout.fillWidth: true
                            wrapMode: TextEdit.Wrap
                            text: (branch.modelData.items || []).join("\n")
                            onTextChanged: if (activeFocus) {
                                dialog.form.subtasks[branch.index].items = text.split("\n").filter(x => x.trim().replace(",", "."));
                                dialog.dirty = true;
                            }
                        }
                        Button {
                            text: qsTr("Убрать из предложения")
                            onClicked: {
                                let updated = dialog.form.subtasks.slice();
                                updated.splice(branch.index, 1);
                                dialog.form = Object.assign({}, dialog.form, {subtasks: updated});
                                weightsInput.text = "";
                                dialog.dirty = true;
                            }
                        }
                    }
                }
                Button {
                    objectName: "draftAddSubtask"
                    text: qsTr("+ Подзадача")
                    onClicked: {
                        dialog.form = Object.assign({}, dialog.form, {subtasks: (dialog.form.subtasks || []).concat([{title: "Новая подзадача", items: []}])});
                        weightsInput.text = "";
                        dialog.dirty = true;
                    }
                }
            }
        }
    }
    footer: Flow {
        spacing: dialog.uiAppearance.dp(8)
        Button {
            objectName: "assistantEdit"
            text: qsTr("Изменить черновик")
            visible: dialog.state.hasDraft && !dialog.editing
            enabled: !dialog.state.busy
            onClicked: dialog.loadDraft()
        }
        Button {
            objectName: "assistantCheck"
            text: qsTr("Проверить изменения")
            visible: dialog.state.hasDraft && dialog.editing
            enabled: !dialog.state.busy
            onClicked: dialog.checkDraft()
        }
        Button {
            objectName: "assistantApply"
            text: qsTr("Подтвердить и сохранить")
            enabled: !!dialog.state.canApply && !dialog.editing && !dialog.dirty && !dialog.taskBackend.busy
            onClicked: dialog.agent.apply()
        }
        Button {
            objectName: "assistantCancel"
            text: dialog.state.hasDraft ? qsTr("Отбросить черновик") : qsTr("Отмена")
            enabled: !dialog.state.saving
            onClicked: { dialog.agent.cancel(); dialog.close(); }
        }
    }
}
