pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "assistantDialog"
    property var connection: null
    required property var agent
    required property var taskBackend
    required property var uiAppearance
    property var state: agent ? agent.state : ({busy: false, hasDraft: false, saving: false})
    property var form: ({})
    property var selectedFiles: []
    property string fileContext: taskBackend.detail.id || ""
    onFileContextChanged: {
        selectedFiles = [];
        if (agent && !state.saving) {
            agent.cancel();
            agent.newChat();
            if (visible) agent.loadFiles(fileContext);
        }
    }
    onOpened: {
        selectedFiles = [];
        if (agent) agent.loadFiles(fileContext);
    }
    property bool dirty: false
    property bool editing: false
    title: state.provider === "chatgpt" ? qsTr("Помощник — подписка ChatGPT") : qsTr("Тестовый помощник — без сети")
    modal: false
    closePolicy: state.saving ? Popup.NoAutoClose : Popup.CloseOnEscape
    onClosed: if (agent) agent.cancel()
    background: Rectangle {
        color: dialog.uiAppearance.colors.surface
        border.color: dialog.uiAppearance.colors.border
        radius: dialog.uiAppearance.dp(14)
    }
    Dialog {
        id: clearHistoryDialog
        objectName: "clearChatHistoryDialog"
        title: qsTr("Удалить всю историю помощника?")
        anchors.centerIn: parent
        width: Math.min(dialog.uiAppearance.dp(460), dialog.width - 24)
        height: Math.min(dialog.uiAppearance.dp(300), dialog.height - 24)
        modal: true
        contentItem: Label {
            text: qsTr("Сообщения удалятся с устройства, текущий диалог сбросится. Задачи и вложения сохранятся.")
            wrapMode: Text.Wrap
        }
        footer: Flow {
            Button {
                objectName: "confirmChatHistoryClear"
                text: qsTr("Удалить историю")
                onClicked: { dialog.agent.clearHistory(); clearHistoryDialog.close(); }
            }
            Button {
                objectName: "cancelChatHistoryClear"
                text: qsTr("Отмена")
                onClicked: clearHistoryDialog.close()
            }
        }
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
                text: dialog.state.provider === "chatgpt"
                    ? qsTr("Запрос и выбранная задача отправятся в OpenAI по вашему действию. Используется подписка ChatGPT. Изменения сохраняются после подтверждения.")
                    : qsTr("Сейчас используется локальный шаблон. Текст запроса и задач не передаётся модели. Подключение аккаунта — ниже.")
            }
            ConnectionPane {
                Layout.fillWidth: true
                visible: !!dialog.connection
                connection: dialog.connection
                agent: dialog.agent
                uiAppearance: dialog.uiAppearance
            }
            CheckBox {
                id: saveHistoryCheck
                objectName: "chatSaveHistory"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                text: qsTr("Сохранять новые сообщения на этом устройстве")
                checked: !!dialog.state.historyEnabled
                enabled: !dialog.state.historyBusy && !dialog.state.saving
                onToggled: dialog.agent.saveHistory(checked)
                contentItem: Label {
                    text: saveHistoryCheck.text
                    leftPadding: saveHistoryCheck.indicator.width + saveHistoryCheck.spacing
                    wrapMode: Text.Wrap
                }
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("История хранится отдельно и не входит в копии задач. Сами вложения и токены в неё не включаются. В следующий запрос входят только последние сообщения текущего обсуждения; прошлые диалоги — после явного выбора «Продолжить». Смена задачи или подключения начинает новый диалог.")
            }
            Label {
                Layout.fillWidth: true
                visible: !!dialog.state.historyError
                text: dialog.state.historyError || ""
                color: dialog.uiAppearance.colors.warning
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
            }
            Flow {
                Layout.fillWidth: true
                spacing: dialog.uiAppearance.dp(8)
                Button {
                    objectName: "chatNew"
                    text: qsTr("Новый диалог")
                    enabled: !dialog.state.busy
                    onClicked: { dialog.agent.newChat(); prompt.text = ""; }
                }
                Button {
                    objectName: "chatClearHistory"
                    text: qsTr("Удалить историю…")
                    enabled: !dialog.state.saving && !dialog.state.historyBusy
                    onClicked: clearHistoryDialog.open()
                }
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("Сообщений в контексте текущего обсуждения: ") + (dialog.state.contextCount || 0)
                wrapMode: Text.Wrap
            }
            CheckBox {
                id: showHistory
                objectName: "chatShowHistory"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                contentItem: Label {
                    text: showHistory.text
                    leftPadding: showHistory.indicator.width + showHistory.spacing
                    wrapMode: Text.Wrap
                }
                text: qsTr("Показать также прошлые диалоги (последние 100 сообщений)")
            }
            Repeater {
                model: (dialog.state.messages || []).filter(x => showHistory.checked || x.chat === dialog.state.chatId)
                delegate: ColumnLayout {
                    id: chatEntry
                    property bool expanded: false
                    required property int index
                    required property var modelData
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        textFormat: Text.PlainText
                        text: (chatEntry.modelData.role === "user" ? qsTr("Вы") : chatEntry.modelData.role === "assistant" ? chatEntry.modelData.label : qsTr("Состояние")) + " · " + Qt.formatDateTime(new Date(chatEntry.modelData.time), "dd.MM.yyyy HH:mm")
                        wrapMode: Text.Wrap
                    }
                    TextArea {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        readOnly: true
                        textFormat: TextEdit.PlainText
                        wrapMode: TextEdit.Wrap
                        text: chatEntry.expanded || chatEntry.modelData.text.length <= 300
                            ? chatEntry.modelData.text : chatEntry.modelData.text.slice(0, 300) + "…"
                        selectByMouse: true
                    }
                    Button {
                        objectName: "chatResume_" + chatEntry.index
                        visible: chatEntry.modelData.role === "assistant" && !!chatEntry.modelData.scope && chatEntry.modelData.chat !== dialog.state.chatId
                        enabled: !dialog.state.busy && !dialog.state.hasDraft
                        text: qsTr("Продолжить этот диалог")
                        onClicked: dialog.agent.resumeChat(chatEntry.modelData.chat,
                            operation.currentIndex === 1 ? (dialog.taskBackend.detail.id || "") : "",
                            operation.currentIndex === 1 ? "split" : "create")
                    }
                    Button {
                        visible: chatEntry.modelData.text.length > 300
                        text: chatEntry.expanded ? qsTr("Свернуть сообщение") : qsTr("Показать полностью")
                        onClicked: chatEntry.expanded = !chatEntry.expanded
                    }
                }
            }
            ComboBox {
                id: operation
                objectName: "assistantOperation"
                Layout.fillWidth: true
                enabled: !dialog.state.busy && !dialog.state.hasDraft
                model: [qsTr("Создать новую задачу"), qsTr("Добавить подзадачи к выбранной задаче")]
                onActivated: dialog.agent.newChat()
            }
            Label {
                Layout.fillWidth: true
                visible: operation.currentIndex === 1
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
                text: qsTr("Выбрано: ") + (dialog.taskBackend.detail.title || qsTr("сначала выберите задачу в списке"))
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("Материалы для помощника: отметьте нужные файлы. Передаётся только их текст, после нажатия кнопки отправки. TXT, Markdown и PDF с текстовым слоем; до 5 файлов и 60 000 символов суммарно.")
            }
            Button {
                objectName: "assistantRefreshFiles"
                text: dialog.state.filesLoading ? qsTr("Загрузка файлов…") : qsTr("Обновить список файлов")
                enabled: !dialog.state.busy && !dialog.state.filesLoading
                onClicked: { dialog.selectedFiles = []; dialog.agent.loadFiles(dialog.fileContext); }
            }
            Label {
                Layout.fillWidth: true
                visible: !!dialog.state.filesError || (!(dialog.state.files || []).length && !dialog.state.filesLoading)
                text: dialog.state.filesError || qsTr("Доступных файлов нет. Добавьте вложение к задаче или файл в «Материалы».")
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }
            Repeater {
                model: dialog.state.files || []
                delegate: CheckBox {
                    id: fileChoice
                    required property var modelData
                    required property int index
                    objectName: "assistantFile_" + index
                    Layout.fillWidth: true
                    enabled: !dialog.state.busy && !dialog.state.hasDraft && modelData.supported
                    text: modelData.name + " — " + modelData.source
                        + (modelData.supported ? "" : qsTr(" (формат пока не поддерживается)"))
                    checked: dialog.selectedFiles.indexOf(modelData.id) >= 0
                    contentItem: Label {
                        text: fileChoice.text
                        textFormat: Text.PlainText
                        leftPadding: fileChoice.indicator.width + fileChoice.spacing
                        wrapMode: Text.Wrap
                    }
                    onToggled: {
                        dialog.selectedFiles = checked
                            ? dialog.selectedFiles.concat([modelData.id])
                            : dialog.selectedFiles.filter(x => x !== modelData.id);
                    }
                }
            }
            Label {
                Layout.fillWidth: true
                visible: dialog.selectedFiles.length > 0
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                text: qsTr("К отправке: ") + (dialog.state.files || []).filter(x => dialog.selectedFiles.indexOf(x.id) >= 0).map(x => x.name).join(", ")
            }
            TextArea {
                id: prompt
                objectName: "assistantPrompt"
                Layout.fillWidth: true
                placeholderText: dialog.state.contextCount > 0 ? qsTr("Уточните пожелания к предложению") : qsTr("Опишите работу")
                wrapMode: TextEdit.Wrap
                enabled: !dialog.state.busy
            }
            Button {
                objectName: "assistantGenerate"
                text: dialog.state.busy ? qsTr("Подготовка…") : (dialog.state.provider === "chatgpt" ? qsTr("Отправить сообщение") : qsTr("Отправить в тестовый чат"))
                enabled: !dialog.state.busy && !dialog.state.hasDraft && !dialog.taskBackend.busy
                    && !dialog.state.filesLoading
                    && (!dialog.connection || !dialog.connection.state.busy)
                    && (operation.currentIndex === 0 || !!dialog.taskBackend.detail.id)
                onClicked: dialog.agent.generate(prompt.text, operation.currentIndex === 1 ? dialog.taskBackend.detail.id : "", dialog.selectedFiles)
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
        Button {
            objectName: "chatRefine"
            text: qsTr("Уточнить предложение")
            visible: dialog.state.hasDraft && !dialog.editing
            enabled: !dialog.state.busy
            onClicked: { dialog.agent.refine(); prompt.text = ""; prompt.forceActiveFocus(); }
        }
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
