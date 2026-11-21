pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

ColumnLayout {
    id: pane
    required property var source
    required property var appearance

    component UiButton: RitmButton { appearance: pane.appearance }
    component UiField: RitmTextField { appearance: pane.appearance }
    property var mailState: source ? source.state : ({busy: false, messages: [], selected: -1})
    property bool writing: !!source && source.proposals.state.saving
    function closeSource() { viewer.close(); }
    Connections {
        target: pane.source
        function onChanged() {
            if (pane.mailState.busy || !pane.mailState.connected)
                consent.checked = false;
        }
    }
    UiButton {
        objectName: "gmailSourceButton"
        text: qsTr("Источники — Gmail")
        enabled: !!pane.source
        onClicked: viewer.open()
    }
    FileDialog {
        id: configPicker
        objectName: "gmailConfigPicker"
        title: qsTr("Конфигурация Google Desktop app")
        nameFilters: [qsTr("Конфигурация JSON (*.json)")]
        onAccepted: pane.source.configure(selectedFile)
    }
    Dialog {
        id: viewer
        objectName: "gmailSourceDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(parent.width - pane.appearance.dp(24), pane.appearance.dp(760))
        height: Math.min(parent.height - pane.appearance.dp(24), pane.appearance.dp(720))
        title: qsTr("Источник Gmail — только чтение")
        modal: true
        closePolicy: pane.source && pane.source.proposals.state.saving ? Popup.NoAutoClose : Popup.CloseOnEscape
        onClosed: { consent.checked = false; pane.source.cancel(); configPicker.close(); }
        background: Rectangle {
            color: pane.appearance.colors.surface
            border.color: pane.appearance.colors.border
            radius: pane.appearance.dialogRadius(12)
        }
        contentItem: ScrollView {
            id: scroll
            clip: true
            contentWidth: availableWidth
            ColumnLayout {
                width: scroll.availableWidth
                spacing: pane.appearance.dp(8)
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: qsTr("Gmail подключается отдельно от модели. Письма читаются вручную и не отмечаются прочитанными. Для передачи выбранного письма модели потребуется отдельное согласие. Просмотр хранится только до закрытия этого окна.")
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: qsTr("Для входа нужен JSON клиента типа Desktop app: проект Google Cloud с Gmail API и разрешением gmail.readonly. В тестовом проекте добавьте свой аккаунт в тестовые пользователи. Вход может потребоваться повторить через 7 дней.")
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: pane.appearance.dp(6)
                    UiButton {
                        objectName: "gmailImportConfig"
                        width: Math.min(implicitWidth, parent.width)
                        text: qsTr("Загрузить конфигурацию Google")
                        enabled: !pane.mailState.busy && !pane.writing && !pane.mailState.connected
                        onClicked: configPicker.open()
                    }
                    UiButton {
                        objectName: "gmailRestore"
                        width: Math.min(implicitWidth, parent.width)
                        text: qsTr("Открыть сохранённое подключение")
                        enabled: !pane.mailState.busy && !pane.writing
                        onClicked: pane.source.restore()
                    }
                    UiButton {
                        objectName: "gmailLogin"
                        width: Math.min(implicitWidth, parent.width)
                        text: qsTr("Войти через Google")
                        enabled: !pane.mailState.busy && !pane.writing && !!pane.mailState.configured
                        onClicked: pane.source.login()
                    }
                    UiButton {
                        objectName: "gmailLogout"
                        width: Math.min(implicitWidth, parent.width)
                        text: qsTr("Удалить подключение с устройства")
                        enabled: !pane.mailState.busy && !pane.writing
                        onClicked: pane.source.logout()
                    }
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                    text: pane.mailState.connected ? qsTr("Аккаунт: ") + pane.mailState.email
                        : pane.mailState.configured ? qsTr("Конфигурация загружена. Можно войти.")
                        : qsTr("Источник не подключён.")
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    visible: !!pane.mailState.warning
                    text: pane.mailState.warning || ""
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: qsTr("Удаление подключения убирает локальные данные входа. Разрешение Google можно отдельно отозвать в настройках своего Google-аккаунта. Файл конфигурации остаётся там, где вы его скачали.")
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: qsTr("Дополнительный фильтр Gmail (пусто — все непрочитанные)")
                }
                UiField {
                    id: query
                    objectName: "gmailQuery"
                    Layout.fillWidth: true
                    placeholderText: "from:teacher@example.org newer_than:30d"
                    maximumLength: 2000
                    enabled: !pane.mailState.busy && !pane.writing
                    onTextEdited: { consent.checked = false; pane.source.cancel(); }
                }
                CheckBox {
                    id: spam
                    objectName: "gmailIncludeSpam"
                    checked: true
                    text: qsTr("Включать спам и корзину Gmail")
                    enabled: !pane.mailState.busy && !pane.writing
                    onToggled: { consent.checked = false; pane.source.cancel(); }
                }
                CheckBox {
                    id: consent
                    objectName: "gmailReadConsent"
                    Layout.fillWidth: true
                    enabled: !!pane.mailState.connected && !pane.mailState.busy && !pane.writing
                    text: qsTr("Разрешаю прочитать непрочитанные письма выбранного аккаунта с этим фильтром")
                    contentItem: Label {
                        text: consent.text
                        leftPadding: consent.indicator.width + consent.spacing
                        wrapMode: Text.Wrap
                    }
                }
                UiButton {
                    objectName: "gmailRead"
                    text: qsTr("Прочитать письма")
                    enabled: consent.enabled && consent.checked
                    onClicked: {
                        consent.checked = false;
                        pane.source.read(query.text, spam.checked, true);
                    }
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    visible: !!pane.mailState.error
                    text: pane.mailState.error || ""
                }
                Label {
                    visible: !!pane.mailState.busy && !pane.writing
                    text: qsTr("Ожидание Gmail… Можно отменить.")
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    visible: !!pane.mailState.loaded
                    text: qsTr("Писем: ") + (pane.mailState.messages || []).length
                        + qsTr(". Недоступны при чтении: ") + (pane.mailState.unavailable || 0)
                }
                Repeater {
                    model: pane.mailState.messages || []
                    delegate: UiButton {
                        id: messageButton
                        required property var modelData
                        required property int index
                        objectName: "gmailMessage_" + index
                        Layout.fillWidth: true
                        onClicked: pane.source.selectMessage(index)
                        contentItem: Label {
                            text: messageButton.modelData.subject + "\n" + messageButton.modelData.sender + "\n" + messageButton.modelData.date
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                        }
                    }
                }
                TextArea {
                    objectName: "gmailMessageBody"
                    Layout.fillWidth: true
                    visible: pane.mailState.selected >= 0
                    readOnly: true
                    wrapMode: TextEdit.Wrap
                    textFormat: TextEdit.PlainText
                    text: pane.mailState.bodyAvailable ? pane.mailState.body
                        : qsTr("Текст недоступен. Вложения автоматически не загружаются.")
                }
                UiButton {
                    text: qsTr("Открыть выбранное письмо в Gmail")
                    visible: !!pane.mailState.sourceUrl
                    onClicked: Qt.openUrlExternally(pane.mailState.sourceUrl)
                }
                MailProposalPane {
                    Layout.fillWidth: true
                    visible: pane.mailState.selected >= 0
                    helper: pane.source ? pane.source.proposals : null
                    appearance: pane.appearance
                    sourceState: pane.mailState
                }
            }
        }
        footer: DialogButtonBox {
            UiButton {
                objectName: "gmailCancel"
                text: qsTr("Отменить")
                enabled: (!!pane.mailState.busy || (!!pane.source && pane.source.proposals.state.busy)) && !pane.writing && !pane.writing
                onClicked: pane.source.cancel()
            }
            UiButton {
                objectName: "gmailClose"
                text: qsTr("Закрыть")
                enabled: !pane.source || !pane.source.proposals.state.saving
                onClicked: viewer.close()
            }
        }
    }
}
