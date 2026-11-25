pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    required property var connection
    required property var agent
    required property var uiAppearance
    property var connectionState: connection ? connection.state : ({})
    property var account: connectionState.copilot || ({})
    property bool available: !!connection && !connectionState.busy && !agent.state.busy
    component UiButton: RitmButton { appearance: pane.uiAppearance }
    component UiCombo: RitmComboBox { appearance: pane.uiAppearance }

    Label {
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
        text: !pane.account.installed
            ? qsTr("Эта установка приложения не содержит подключения Copilot.")
            : pane.account.connected
            ? qsTr("GitHub: ") + pane.account.login
            : qsTr("Войдите через официальный GitHub Copilot. Вход откроется в браузере; нужен доступный план Copilot.")
    }
    Label {
        objectName: "copilotCode"
        Layout.fillWidth: true
        visible: !!pane.account.code
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
        text: qsTr("Код для входа: ") + (pane.account.code || "")
    }
    UiButton {
        visible: !!pane.account.code
        text: qsTr("Открыть страницу ввода кода")
        onClicked: Qt.openUrlExternally("https://github.com/login/device")
    }
    Label {
        Layout.fillWidth: true
        visible: !!pane.connectionState.error
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
        text: pane.connectionState.error || ""
        color: pane.uiAppearance.colors.warning
    }
    Flow {
        Layout.fillWidth: true
        spacing: pane.uiAppearance.dp(8)
        UiButton {
            objectName: "copilotLogin"
            text: qsTr("Войти через GitHub")
            enabled: pane.available && !!pane.account.installed
            onClicked: pane.connection.copilotLogin()
        }
        UiButton {
            objectName: "copilotRestore"
            text: qsTr("Проверить сохранённый вход")
            enabled: pane.available && !!pane.account.installed
            onClicked: pane.connection.copilotRestore()
        }
        UiButton {
            objectName: "copilotCancel"
            text: qsTr("Отменить вход")
            enabled: !!pane.connectionState.busy
            onClicked: pane.connection.cancel()
        }
    }
    UiCombo {
        objectName: "copilotModel"
        Layout.fillWidth: true
        model: pane.account.models || []
        textRole: "display_name"
        valueRole: "slug"
        currentIndex: (pane.account.models || []).findIndex(x => x.slug === pane.account.model)
        enabled: pane.available && !!pane.account.connected
        onActivated: pane.connection.selectCopilotModel(currentValue)
    }
    Flow {
        Layout.fillWidth: true
        spacing: pane.uiAppearance.dp(8)
        UiButton {
            objectName: "copilotUse"
            text: qsTr("Использовать Copilot")
            enabled: pane.available && !!pane.account.connected && !!pane.account.model
            onClicked: pane.connection.useCopilot()
        }
        UiButton {
            objectName: "copilotMock"
            text: qsTr("Локальный тест без сети")
            enabled: pane.available
            onClicked: pane.connection.useMock()
        }
        UiButton {
            objectName: "copilotDisconnect"
            text: qsTr("Отключить в Ритме")
            enabled: pane.available && !!pane.account.connected
            onClicked: pane.connection.disconnectCopilot()
        }
    }
    Label {
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        text: qsTr("Проверка входа загружает список моделей, но не отправляет запрос модели. Copilot пока принимает только текст. Запросы расходуют лимиты выбранного аккаунта; платный API автоматически не включается.")
    }
    Label {
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        text: qsTr("Отключение в Ритме прекращает использование подключения. Сохранённый вход Copilot остаётся в системном хранилище; разрешение GitHub можно отозвать в настройках аккаунта GitHub. Если защищённое хранилище недоступно, вход может не сохраниться.")
    }
}
