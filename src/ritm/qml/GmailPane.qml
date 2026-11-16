pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

ColumnLayout {
    id: pane
    required property var source
    required property var appearance
    property var mailState: source ? source.state : ({busy: false, messages: []})
    function closeSource() { viewer.close(); }
    Connections {
        target: pane.source
        function onChanged() {
            if (pane.mailState.busy || !pane.mailState.connected)
                consent.checked = false;
        }
    }
    Button {
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
        onClosed: { consent.checked = false; pane.source.cancel(); configPicker.close(); }
        background: Rectangle {
            color: pane.appearance.colors.surface
            border.color: pane.appearance.colors.border
            radius: pane.appearance.dp(12)
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
                    text: qsTr("Gmail подключается отдельно от модели. Письма читаются вручную, не отмечаются прочитанными и пока не передаются ИИ. Просмотр хранится только до закрытия этого окна.")
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: qsTr("Для входа нужен JSON клиента типа Desktop app: проект Google Cloud с Gmail API и разрешением gmail.readonly. В тестовом проекте добавьте свой аккаунт в тестовые пользователи. Вход может потребоваться повторить через 7 дней.")
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: pane.appearance.dp(6)
                    Button {
                        objectName: "gmailImportConfig"
                        width: Math.min(implicitWidth, parent.width)
                        text: qsTr("Загрузить конфигурацию Google")
                        enabled: !pane.mailState.busy && !pane.mailState.connected
                        onClicked: configPicker.open()
                    }
                    Button {
                        objectName: "gmailRestore"
                        width: Math.min(implicitWidth, parent.width)
                        text: qsTr("Открыть сохранённое подключение")
                        enabled: !pane.mailState.busy
                        onClicked: pane.source.restore()
                    }
                    Button {
                        objectName: "gmailLogin"
                        width: Math.min(implicitWidth, parent.width)
                        text: qsTr("Войти через Google")
                        enabled: !pane.mailState.busy && !!pane.mailState.configured
                        onClicked: pane.source.login()
                    }
                    Button {
                        objectName: "gmailLogout"
                        width: Math.min(implicitWidth, parent.width)
                        text: qsTr("Удалить подключение с устройства")
                        enabled: !pane.mailState.busy
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
                TextField {
                    id: query
                    objectName: "gmailQuery"
                    Layout.fillWidth: true
                    placeholderText: "from:teacher@example.org newer_than:30d"
                    maximumLength: 2000
                    enabled: !pane.mailState.busy
                    onTextEdited: { consent.checked = false; pane.source.cancel(); }
                }
                CheckBox {
                    id: spam
                    objectName: "gmailIncludeSpam"
                    checked: true
                    text: qsTr("Включать спам и корзину Gmail")
                    enabled: !pane.mailState.busy
                    onToggled: { consent.checked = false; pane.source.cancel(); }
                }
                CheckBox {
                    id: consent
                    objectName: "gmailReadConsent"
                    Layout.fillWidth: true
                    enabled: !!pane.mailState.connected && !pane.mailState.busy
                    text: qsTr("Разрешаю прочитать непрочитанные письма выбранного аккаунта с этим фильтром")
                    contentItem: Label {
                        text: consent.text
                        leftPadding: consent.indicator.width + consent.spacing
                        wrapMode: Text.Wrap
                    }
                }
                Button {
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
                    visible: !!pane.mailState.busy
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
                    delegate: Button {
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
            }
        }
        footer: DialogButtonBox {
            Button {
                objectName: "gmailCancel"
                text: qsTr("Отменить")
                enabled: !!pane.mailState.busy
                onClicked: pane.source.cancel()
            }
            Button {
                objectName: "gmailClose"
                text: qsTr("Закрыть")
                onClicked: viewer.close()
            }
        }
    }
}
