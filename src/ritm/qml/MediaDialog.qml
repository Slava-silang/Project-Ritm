pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    required property var media
    required property var uiAppearance
    property string context: ""
    property string fileId: ""
    property int turns: 0
    property bool dirty: false
    readonly property var snapshot: media ? media.state : ({busy: false})
    objectName: "mediaDialog"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(uiAppearance.dp(700), parent.width - 24)
    height: Math.min(uiAppearance.dp(680), parent.height - 24)
    modal: true
    title: qsTr("Предпросмотр изображения")
    closePolicy: snapshot.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
    function showFile(identifier) {
        fileId = identifier;
        turns = 0;
        cropX.value = 0; cropY.value = 0; cropW.value = 100; cropH.value = 100;
        open();
        render(false);
    }
    function render(save) {
        dirty = false;
        media.preview(context, fileId, turns, [cropX.value, cropY.value, cropW.value, cropH.value], save);
    }
    contentItem: ScrollView {
        id: scroll
        clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: scroll.availableWidth
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
                text: dialog.snapshot.name || ""
            }
            Image {
                objectName: "mediaPreviewImage"
                Layout.fillWidth: true
                Layout.preferredHeight: dialog.uiAppearance.dp(240)
                source: dialog.snapshot.url || ""
                fillMode: Image.PreserveAspectFit
                cache: false
                asynchronous: true
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: dialog.snapshot.busy ? qsTr("Подготовка…")
                    : dialog.snapshot.error || ((dialog.snapshot.width || 0) + " × " + (dialog.snapshot.height || 0))
            }
            Button {
                objectName: "mediaRotate"
                text: qsTr("Повернуть на 90°")
                enabled: !dialog.snapshot.busy
                onClicked: { dialog.turns = (dialog.turns + 1) % 4; dialog.render(false); }
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("Обрезка в процентах от повёрнутого изображения. Исходный файл сохранится.")
                wrapMode: Text.Wrap
            }
            GridLayout {
                columns: 2
                enabled: !dialog.snapshot.busy
                Label { text: qsTr("Слева, %") }
                SpinBox { id: cropX; objectName: "mediaCropX"; from: 0; to: 99; editable: true; onValueModified: dialog.dirty = true }
                Label { text: qsTr("Сверху, %") }
                SpinBox { id: cropY; objectName: "mediaCropY"; from: 0; to: 99; editable: true; onValueModified: dialog.dirty = true }
                Label { text: qsTr("Ширина, %") }
                SpinBox { id: cropW; objectName: "mediaCropWidth"; from: 1; to: 100; editable: true; onValueModified: dialog.dirty = true }
                Label { text: qsTr("Высота, %") }
                SpinBox { id: cropH; objectName: "mediaCropHeight"; from: 1; to: 100; editable: true; onValueModified: dialog.dirty = true }
            }
            Button {
                objectName: "mediaRefreshPreview"
                text: qsTr("Обновить предпросмотр")
                enabled: !dialog.snapshot.busy
                onClicked: dialog.render(false)
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("Изменённое изображение сохраняется отдельной копией в «Материалы». Отправка модели — только после выбора и подтверждения сообщения.")
                wrapMode: Text.Wrap
            }
        }
    }
    footer: DialogButtonBox {
        Button {
            objectName: "mediaClose"
            text: qsTr("Закрыть")
            enabled: !dialog.snapshot.busy
            onClicked: dialog.close()
        }
        Button {
            objectName: "mediaSaveCopy"
            text: qsTr("Сохранить копию")
            enabled: !dialog.snapshot.busy && !dialog.dirty && !!dialog.snapshot.url && !dialog.snapshot.error
            onClicked: dialog.render(true)
        }
    }
    Connections {
        target: dialog.media
        function onImported(identifier) { dialog.close(); }
    }
}
