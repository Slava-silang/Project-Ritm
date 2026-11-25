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
    property var localState: connectionState.ollama || ({})
    property bool available: !!connection && !connectionState.busy && !agent.state.busy
    component UiButton: RitmButton { appearance: pane.uiAppearance }
    component UiCombo: RitmComboBox { appearance: pane.uiAppearance }

    Label {
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        text: qsTr("Запустите Ollama на этом компьютере и заранее установите текстовую модель. Ритм не скачивает модели и не запускает сервер автоматически.")
    }
    Label {
        objectName: "ollamaError"
        Layout.fillWidth: true
        visible: !!pane.connectionState.error
        text: pane.connectionState.error || ""
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: pane.uiAppearance.colors.warning
    }
    Flow {
        Layout.fillWidth: true
        spacing: pane.uiAppearance.dp(8)
        UiButton {
            objectName: "ollamaModels"
            text: qsTr("Загрузить список моделей")
            enabled: pane.available
            onClicked: pane.connection.ollamaModels()
        }
        UiButton {
            objectName: "ollamaCancel"
            text: qsTr("Отменить загрузку списка")
            enabled: !!pane.connectionState.busy
            onClicked: pane.connection.cancel()
        }
    }
    Label {
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        text: (pane.localState.models || []).length
            ? qsTr("Выберите модель из найденных на компьютере.")
            : qsTr("Список пуст. Загрузите его после запуска Ollama. Облачные модели здесь не показываются.")
    }
    UiCombo {
        objectName: "ollamaModel"
        Layout.fillWidth: true
        model: pane.localState.models || []
        textRole: "display_name"
        valueRole: "slug"
        currentIndex: (pane.localState.models || []).findIndex(x => x.slug === pane.localState.model)
        enabled: pane.available && count > 0
        onActivated: pane.connection.selectOllamaModel(currentValue)
    }
    Flow {
        Layout.fillWidth: true
        spacing: pane.uiAppearance.dp(8)
        UiButton {
            objectName: "ollamaUse"
            text: qsTr("Использовать локальную модель")
            enabled: pane.available && !!pane.localState.model
            onClicked: pane.connection.useOllama()
        }
        UiButton {
            objectName: "ollamaDisconnect"
            text: qsTr("Отключить в Ритме")
            enabled: pane.available && (pane.localState.models || []).length > 0
            onClicked: pane.connection.disconnectOllama()
        }
        UiButton {
            objectName: "ollamaMock"
            text: qsTr("Локальный тест без модели")
            enabled: pane.available
            onClicked: pane.connection.useMock()
        }
    }
    Label {
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        text: qsTr("Загрузка списка не отправляет задачи модели. Перед отправкой запроса проверяются её локальные метаданные. Пока поддерживаются только текстовые материалы. API-ключи не используются.")
    }
    Label {
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        text: qsTr("Отключение в Ритме не останавливает Ollama и не удаляет модели. После перезапуска приложения подключение нужно выбрать заново.")
    }
}
