pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    required property var connection
    required property var agent
    required property var uiAppearance
    property var accountState: connection ? connection.state : ({busy: false})
    property bool available: !!connection && !accountState.busy && !agent.state.busy
    Button {
        objectName: "connectionExpand"
        text: controls.visible ? qsTr("Скрыть подключение") : qsTr("Подключение ChatGPT")
        onClicked: controls.visible = !controls.visible
    }
    ColumnLayout {
        id: controls
        visible: false
        Layout.fillWidth: true
        Label {
            Layout.fillWidth: true
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            text: pane.accountState.connected ? qsTr("Аккаунт: ") + (pane.accountState.email || qsTr("ChatGPT")) : qsTr("Аккаунт не подключён. Вход откроется в браузере. Нужен подходящий план; доступ проверяется у OpenAI.")
        }
        Label {
            Layout.fillWidth: true
            visible: !!pane.accountState.connected
            wrapMode: Text.Wrap
            text: pane.accountState.sharing ? qsTr("Разрешение использовать подписку получено.") : qsTr("Разрешение использовать подписку не получено. Запросы недоступны.")
        }
        Label {
            Layout.fillWidth: true
            visible: !!pane.accountState.warning || !!pane.accountState.error
            text: pane.accountState.error || pane.accountState.warning || ""
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: pane.uiAppearance.colors.warning
        }
        Flow {
            Layout.fillWidth: true
            spacing: pane.uiAppearance.dp(8)
            Button {
                objectName: "chatgptLogin"
                text: qsTr("Continue with ChatGPT")
                enabled: pane.available
                onClicked: pane.connection.login()
            }
            Button {
                objectName: "chatgptRestore"
                text: qsTr("Восстановить сохранённый вход")
                enabled: pane.available
                onClicked: pane.connection.restore()
            }
            Button {
                objectName: "chatgptModels"
                text: qsTr("Загрузить доступные модели")
                enabled: pane.available && !!pane.accountState.sharing
                onClicked: pane.connection.models()
            }
        }
        ComboBox {
            objectName: "chatgptModel"
            Layout.fillWidth: true
            model: pane.accountState.models || []
            textRole: "display_name"
            valueRole: "slug"
            enabled: pane.available
            currentIndex: (pane.accountState.models || []).findIndex(x => x.slug === pane.accountState.model)
            onActivated: pane.connection.selectModel(currentValue)
        }
        Flow {
            Layout.fillWidth: true
            spacing: pane.uiAppearance.dp(8)
            Button {
                objectName: "chatgptUse"
                text: qsTr("Использовать ChatGPT")
                enabled: pane.available && !!pane.accountState.model && !!pane.accountState.sharing
                onClicked: pane.connection.useChatGPT()
            }
            Button {
                objectName: "chatgptMock"
                text: qsTr("Локальный тест без сети")
                enabled: pane.available
                onClicked: pane.connection.useMock()
            }
            Button {
                objectName: "chatgptLogout"
                text: qsTr("Выйти из аккаунта")
                enabled: pane.available
                onClicked: pane.connection.logout()
            }
            Button {
                text: qsTr("Отменить подключение")
                enabled: !!pane.accountState.busy
                onClicked: pane.connection.cancel()
            }
            Button {
                text: qsTr("Открыть ChatGPT для проверки лимитов")
                onClicked: Qt.openUrlExternally("https://chatgpt.com/")
            }
        }
        CheckBox {
            id: consent
            objectName: "chatgptProbeConsent"
            Layout.fillWidth: true
            text: qsTr("Разрешаю тестовый запрос без моих задач; он расходует лимит подписки")
            enabled: pane.available && !!pane.accountState.sharing && !!pane.accountState.model
            contentItem: Label {
                text: consent.text
                leftPadding: consent.indicator.width + consent.spacing
                wrapMode: Text.Wrap
            }
        }
        Button {
            objectName: "chatgptProbe"
            text: qsTr("Проверить запрос по подписке")
            enabled: consent.enabled && consent.checked
            onClicked: { consent.checked = false; pane.connection.probe(); }
        }
        Label {
            Layout.fillWidth: true
            visible: !!pane.accountState.verified
            text: qsTr("Запрос по подписке завершён успешно.")
            wrapMode: Text.Wrap
        }
        Label {
            Layout.fillWidth: true
            text: qsTr("Вход и список моделей ещё не подтверждают доступ к запросам. При лимите платный API не включается. Сейчас поддерживается одна активная регистрация аккаунта.")
            wrapMode: Text.Wrap
        }
    }
}
