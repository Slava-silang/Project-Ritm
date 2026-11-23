pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

RitmDialog {
    id: dialog

    component UiArea: RitmTextArea { appearance: dialog.uiAppearance }

    component UiCheck: RitmCheckBox { appearance: dialog.uiAppearance }
    component UiFooter: RitmDialogFooter { appearance: dialog.uiAppearance }
    appearance: dialog.uiAppearance
    objectName: "assistantDialog"
    property var connection: null
    required property var agent
    required property var taskBackend
    required property var uiAppearance

    component UiButton: RitmButton { appearance: dialog.uiAppearance }
    component UiField: RitmTextField { appearance: dialog.uiAppearance }
    component UiCombo: RitmComboBox { appearance: dialog.uiAppearance }
    component UiDialog: RitmDialog { appearance: dialog.uiAppearance }
    property var state: agent ? agent.state : ({busy: false, hasDraft: false, saving: false})
    property var form: ({})
    property var selectedFiles: []
    property string pendingFile: ""
    property string catalogConnection: ""
    property string fileContext: taskBackend.detail.id || ""
    onFileContextChanged: {
        selectedFiles = [];
        pendingFile = "";
        mediaEditor.close();
        if (agent) agent.media.reset();
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
    dim: dialog.uiAppearance.usesSharedStyle
    Overlay.modeless: Rectangle {
        color: "#50101517"
    }
    closePolicy: state.saving ? Popup.NoAutoClose : Popup.CloseOnEscape
    onClosed: if (agent) { agent.cancel(); agent.media.reset(); mediaEditor.close(); gmailPane.closeSource(); }
    background: RitmControlBackground {
        appearance: dialog.uiAppearance
        kind: "dialog"
        legacyColor: dialog.uiAppearance.colors.surface
        legacyBorder: dialog.uiAppearance.colors.border
        legacyBorderWidth: 1
        radius: dialog.uiAppearance.dialogRadius(14)
    }
    UiDialog {
        id: clearHistoryDialog
        objectName: "clearChatHistoryDialog"
        title: qsTr("Удалить всю историю помощника?")
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(dialog.uiAppearance.dp(460), dialog.width - 24)
        height: Math.min(dialog.uiAppearance.dp(300), dialog.height - 24)
        modal: true
        contentItem: Label {
            text: qsTr("Сообщения удалятся с устройства, текущий диалог сбросится. Задачи и вложения сохранятся.")
            wrapMode: Text.Wrap
        }
        footer: UiFooter {
            UiButton {
                objectName: "confirmChatHistoryClear"
                text: qsTr("Удалить историю")
                onClicked: { dialog.agent.clearHistory(); clearHistoryDialog.close(); }
            }
            UiButton {
                objectName: "cancelChatHistoryClear"
                text: qsTr("Отмена")
                onClicked: clearHistoryDialog.close()
            }
        }
    }
    FileDialog {
        id: assistantFilePicker
        objectName: "assistantFilePicker"
        title: qsTr("Добавить файл для помощника")
        onAccepted: dialog.agent.media.importFile(selectedFile)
    }
    MediaDialog {
        id: mediaEditor
        media: dialog.agent ? dialog.agent.media : null
        uiAppearance: dialog.uiAppearance
        context: dialog.fileContext
    }
    UiDialog {
        id: resendDialog
        objectName: "assistantResendDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(dialog.uiAppearance.dp(480), parent.width - 24)
        height: Math.min(dialog.uiAppearance.dp(280), parent.height - 24)
        modal: true
        title: qsTr("Повторно передать файлы?")
        contentItem: Label {
            text: qsTr("Выбранные файлы снова получит текущая модель. Файлы из прежних сообщений автоматически не добавляются.")
            wrapMode: Text.Wrap
        }
        footer: UiFooter {
            UiButton { objectName: "assistantCancelResend"; text: qsTr("Отмена"); onClicked: resendDialog.close() }
            UiButton {
                objectName: "assistantConfirmResend"
                text: qsTr("Передать и отправить")
                onClicked: { resendDialog.close(); dialog.send(true); }
            }
        }
    }
    function send(confirmed) {
        agent.generate(prompt.text, operation.currentIndex === 1 ? taskBackend.detail.id : "", selectedFiles, confirmed,
            operation.currentIndex === 2 ? "update" : operation.currentIndex === 1 ? "split" : "create");
    }
    Connections {
        target: dialog.agent ? dialog.agent.media : null
        function onImported(identifier) {
            dialog.pendingFile = identifier;
            dialog.agent.loadFiles(dialog.fileContext);
            dialog.taskBackend.reload();
        }
    }
    function loadDraft() {
        form = JSON.parse(JSON.stringify(state.draft || {}));
        const fields = state.updateMode ? state.editFields : form;
        titleInput.text = fields.title || "";
        descriptionInput.text = fields.description || "";
        difficultyInput.text = fields.difficulty ? String(fields.difficulty) : "";
        minutesInput.text = fields.expected_minutes ? String(fields.expected_minutes) : "";
        deadlineInput.text = state.deadlineText || "";
        weightsInput.text = (form.weights || []).join("; ");
        dirty = false;
        editing = true;
    }
    function checkDraft() {
        let proposal = JSON.parse(JSON.stringify(form));
        if (state.updateMode) {
            proposal.changes = Object.assign({}, proposal.changes || {}, {
                title: titleInput.text, description: descriptionInput.text,
                difficulty: difficultyInput.text.trim() || null,
                expected_minutes: minutesInput.text.trim() || null
            });
        } else if (!state.split) {
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
            const key = dialog.state.provider + "/" + dialog.state.model;
            if (key !== dialog.catalogConnection) {
                dialog.catalogConnection = key;
                dialog.selectedFiles = [];
                if (dialog.visible && !dialog.state.busy && !dialog.state.filesLoading)
                    dialog.agent.loadFiles(dialog.fileContext);
            }
            if (dialog.pendingFile && !dialog.state.filesLoading) {
                const row = (dialog.state.files || []).find(x => x.id === dialog.pendingFile);
                if (row && row.supported) dialog.selectedFiles = dialog.selectedFiles.concat([row.id]);
                dialog.pendingFile = "";
            }
            if (!dialog.state.hasDraft)
                dialog.editing = false;
        }
        function onApplied(identifier) {
            dialog.taskBackend.reload();
            dialog.close();
        }
        function onMailApplied() { dialog.taskBackend.reload(); }
    }
    contentItem: ScrollView {
        id: scroll
        clip: true
        contentWidth: availableWidth
        rightPadding: dialog.uiAppearance.usesSharedStyle ? dialog.uiAppearance.dp(12) : 0
        ScrollBar.vertical.policy: dialog.uiAppearance.usesSharedStyle && assistantForm.implicitHeight > availableHeight + 1
            ? ScrollBar.AlwaysOn : ScrollBar.AsNeeded
        ScrollBar.vertical.palette.mid: dialog.uiAppearance.colors.scrollbar ?? dialog.palette.mid
        ScrollBar.vertical.palette.dark: dialog.uiAppearance.colors.scrollbar ?? dialog.palette.dark
        ColumnLayout {
            id: assistantForm
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
            SkillPane {
                Layout.fillWidth: true
                library: dialog.agent ? dialog.agent.skills : null
                appearance: dialog.uiAppearance
                prefix: "assistant"
                blocked: !!dialog.state.busy
            }
            GmailPane {
                id: gmailPane
                Layout.fillWidth: true
                source: dialog.agent ? dialog.agent.gmail : null
                appearance: dialog.uiAppearance
            }
            UiCheck {
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
                UiButton {
                    objectName: "chatNew"
                    text: qsTr("Новый диалог")
                    enabled: !dialog.state.busy
                    onClicked: { dialog.agent.newChat(); prompt.text = ""; }
                }
                UiButton {
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
            UiCheck {
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
                    UiArea {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        readOnly: true
                        textFormat: TextEdit.PlainText
                        wrapMode: TextEdit.Wrap
                        text: chatEntry.expanded || chatEntry.modelData.text.length <= 300
                            ? chatEntry.modelData.text : chatEntry.modelData.text.slice(0, 300) + "…"
                        selectByMouse: true
                    }
                    UiButton {
                        objectName: "chatResume_" + chatEntry.index
                        visible: chatEntry.modelData.role === "assistant" && !!chatEntry.modelData.scope && chatEntry.modelData.chat !== dialog.state.chatId
                        enabled: !dialog.state.busy && !dialog.state.hasDraft
                        text: qsTr("Продолжить этот диалог")
                        onClicked: dialog.agent.resumeChat(chatEntry.modelData.chat,
                            operation.currentIndex === 1 ? (dialog.taskBackend.detail.id || "") : "",
                            operation.currentIndex === 2 ? "update" : operation.currentIndex === 1 ? "split" : "create")
                    }
                    UiButton {
                        visible: chatEntry.modelData.text.length > 300
                        text: chatEntry.expanded ? qsTr("Свернуть сообщение") : qsTr("Показать полностью")
                        onClicked: chatEntry.expanded = !chatEntry.expanded
                    }
                }
            }
            UiCombo {
                id: operation
                objectName: "assistantOperation"
                Layout.fillWidth: true
                enabled: !dialog.state.busy && !dialog.state.hasDraft
                model: [qsTr("Создать новую задачу"), qsTr("Добавить подзадачи к выбранной задаче"), qsTr("Найти и изменить задачу")]
                onActivated: dialog.agent.newChat()
            }
            Label {
                Layout.fillWidth: true
                visible: operation.currentIndex === 2
                wrapMode: Text.Wrap
                text: qsTr("В этом режиме модель получит названия, описания, сроки и состояние всех активных задач с подзадачами. Корзина, завершённые главные задачи и файлы автоматически не передаются. Можно спросить, существует ли задача, или предложить её изменение. Правки одной задачи сохраняются после проверки и подтверждения.")
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
                text: qsTr("Отметьте файлы для текущего сообщения: до 5 файлов, до 8 МиБ каждый и до 20 МиБ суммарно. Изображения и документы передаются целиком, если это поддерживает выбранная модель. Иначе доступен прежний текстовый режим TXT/MD/PDF (60 000 символов). Выбранные материалы прикрепятся к задаче после подтверждения предложения.")
            }
            UiButton {
                objectName: "assistantAddFile"
                text: qsTr("Добавить файл…")
                enabled: !dialog.state.busy && !dialog.state.hasDraft && (!dialog.agent || !dialog.agent.media.state.busy)
                onClicked: assistantFilePicker.open()
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: dialog.agent ? dialog.agent.media.state.error || "" : ""
                visible: text.length > 0
            }
            UiButton {
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
                delegate: ColumnLayout {
                    id: fileRow
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    UiCheck {
                        id: fileChoice
                        objectName: "assistantFile_" + fileRow.index
                        Layout.fillWidth: true
                        enabled: !dialog.state.busy && !dialog.state.hasDraft && fileRow.modelData.supported
                        text: fileRow.modelData.name + " — " + fileRow.modelData.source
                            + (fileRow.modelData.supported ? "" : qsTr(" (формат или модель не поддерживается; можно сменить модель)"))
                        checked: dialog.selectedFiles.indexOf(fileRow.modelData.id) >= 0
                        contentItem: Label {
                            text: fileChoice.text
                            textFormat: Text.PlainText
                            leftPadding: fileChoice.indicator.width + fileChoice.spacing
                            wrapMode: Text.Wrap
                        }
                        onToggled: {
                            dialog.selectedFiles = checked
                                ? dialog.selectedFiles.concat([fileRow.modelData.id])
                                : dialog.selectedFiles.filter(x => x !== fileRow.modelData.id);
                        }
                    }
                    UiButton {
                        objectName: "assistantPreview_" + fileRow.index
                        visible: !!fileRow.modelData.image
                        text: qsTr("Посмотреть / обрезать")
                        enabled: !dialog.state.busy && !dialog.state.hasDraft && (!dialog.agent || !dialog.agent.media.state.busy)
                        onClicked: mediaEditor.showFile(fileRow.modelData.id)
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
            UiArea {
                id: prompt
                objectName: "assistantPrompt"
                Layout.fillWidth: true
                placeholderText: dialog.state.contextCount > 0 ? qsTr("Уточните пожелания к предложению") : qsTr("Опишите работу")
                wrapMode: TextEdit.Wrap
                enabled: !dialog.state.busy
            }
            UiButton {
                objectName: "assistantGenerate"
                text: dialog.state.busy ? qsTr("Подготовка…") : (dialog.state.provider === "chatgpt" ? qsTr("Отправить сообщение") : qsTr("Отправить в тестовый чат"))
                enabled: !dialog.state.busy && !dialog.state.hasDraft && !dialog.taskBackend.busy
                    && !dialog.state.filesLoading && (!dialog.agent || !dialog.agent.media.state.busy)
                    && (!dialog.agent || !dialog.agent.skills.state.busy)
                    && (!dialog.connection || !dialog.connection.state.busy)
                    && (operation.currentIndex !== 1 || !!dialog.taskBackend.detail.id)
                onClicked: {
                    if (dialog.selectedFiles.length > 0 && dialog.state.contextCount > 0) resendDialog.open();
                    else dialog.send(false);
                }
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
                objectName: "assistantDraftPreview"
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
                UiField {
                    id: titleInput
                    objectName: "draftTitle"
                    Layout.fillWidth: true
                    visible: !dialog.state.split
                    onTextEdited: dialog.dirty = true
                }
                Label { text: qsTr("Описание"); visible: !dialog.state.split }
                UiArea {
                    id: descriptionInput
                    Layout.fillWidth: true
                    visible: !dialog.state.split
                    wrapMode: TextEdit.Wrap
                    onTextChanged: if (activeFocus) dialog.dirty = true
                }
                Label { text: qsTr("Сложность 1–10 (необязательно)"); visible: !dialog.state.split }
                UiField {
                    id: difficultyInput
                    objectName: "draftDifficulty"
                    Layout.fillWidth: true
                    visible: !dialog.state.split
                    onTextEdited: dialog.dirty = true
                }
                Label { text: qsTr("Ожидаемое время, минуты"); visible: !dialog.state.split }
                UiField {
                    id: minutesInput
                    objectName: "draftMinutes"
                    Layout.fillWidth: true
                    visible: !dialog.state.split
                    onTextEdited: dialog.dirty = true
                }
                Label { text: qsTr("Дедлайн: ДД.ММ.ГГГГ ЧЧ:ММ или пусто"); visible: !dialog.state.split }
                DateTimeField {
                    appearance: dialog.uiAppearance
                    allowEmpty: true
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
                UiField {
                    id: weightsInput
                    objectName: "draftWeights"
                    Layout.fillWidth: true
                    onTextEdited: dialog.dirty = true
                }
                UiCheck {
                    objectName: "draftRootCompleted"
                    visible: !!dialog.state.updateMode && Object.prototype.hasOwnProperty.call(dialog.form.changes || {}, "completed")
                    text: qsTr("Выполнить всю задачу (с подзадачами)")
                    checked: !!(dialog.form.changes || {}).completed
                    onToggled: { dialog.form.changes.completed = checked; dialog.dirty = true; }
                }
                Repeater {
                    model: dialog.state.updateMode ? dialog.form.nodes || [] : []
                    delegate: ColumnLayout {
                        id: nodeEdit
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true
                        Label {
                            Layout.fillWidth: true
                            wrapMode: Text.Wrap
                            text: qsTr("Правка существующего пункта ") + (nodeEdit.index + 1)
                        }
                        UiField {
                            objectName: "draftNodeTitle_" + nodeEdit.index
                            Layout.fillWidth: true
                            visible: Object.prototype.hasOwnProperty.call(nodeEdit.modelData, "title")
                            text: nodeEdit.modelData.title || ""
                            onTextEdited: { dialog.form.nodes[nodeEdit.index].title = text; dialog.dirty = true; }
                        }
                        UiCheck {
                            objectName: "draftNodeCompleted_" + nodeEdit.index
                            visible: Object.prototype.hasOwnProperty.call(nodeEdit.modelData, "completed")
                            text: qsTr("Отметить пункт выполненным")
                            checked: !!nodeEdit.modelData.completed
                            onToggled: { dialog.form.nodes[nodeEdit.index].completed = checked; dialog.dirty = true; }
                        }
                    }
                }
                Repeater {
                    model: dialog.form.subtasks || []
                    delegate: ColumnLayout {
                        id: branch
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true
                        Label { text: qsTr("Новая подзадача ") + (branch.index + 1) }
                        UiField {
                            objectName: "draftSubtask_" + branch.index
                            Layout.fillWidth: true
                            text: branch.modelData.title
                            onTextEdited: { dialog.form.subtasks[branch.index].title = text; dialog.dirty = true; }
                        }
                        Label { text: qsTr("Пункты второго уровня, каждый с новой строки") }
                        UiArea {
                            objectName: "draftItems_" + branch.index
                            Layout.fillWidth: true
                            wrapMode: TextEdit.Wrap
                            text: (branch.modelData.items || []).join("\n")
                            onTextChanged: if (activeFocus) {
                                dialog.form.subtasks[branch.index].items = text.split("\n").filter(x => x.trim().replace(",", "."));
                                dialog.dirty = true;
                            }
                        }
                        UiButton {
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
                UiButton {
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
    footer: UiFooter {
        UiButton {
            objectName: "chatRefine"
            text: qsTr("Уточнить предложение")
            visible: dialog.state.hasDraft && !dialog.editing
            enabled: !dialog.state.busy
            onClicked: { dialog.agent.refine(); prompt.text = ""; prompt.forceActiveFocus(); }
        }
        UiButton {
            objectName: "assistantEdit"
            text: qsTr("Изменить черновик")
            visible: dialog.state.hasDraft && !dialog.editing
            enabled: !dialog.state.busy
            onClicked: dialog.loadDraft()
        }
        UiButton {
            objectName: "assistantCheck"
            text: qsTr("Проверить изменения")
            visible: dialog.state.hasDraft && dialog.editing
            enabled: !dialog.state.busy
            onClicked: dialog.checkDraft()
        }
        UiButton {
            objectName: "assistantApply"
            text: qsTr("Подтвердить и сохранить")
            enabled: !!dialog.state.canApply && !dialog.editing && !dialog.dirty && !dialog.taskBackend.busy
            onClicked: dialog.agent.apply()
        }
        UiButton {
            objectName: "assistantCancel"
            text: dialog.state.hasDraft ? qsTr("Отбросить черновик") : qsTr("Отмена")
            enabled: !dialog.state.saving
            onClicked: { dialog.agent.cancel(); dialog.close(); }
        }
    }
}
