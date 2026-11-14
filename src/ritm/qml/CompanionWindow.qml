import QtQuick

Window {
    id: petWindow
    required property var companion
    readonly property bool imageReady: petImage.status === Image.Ready
    title: qsTr("Ритм — компаньон")
    objectName: "companionWindow"
    visible: false
    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
    width: companion.state.size
    height: width + 100
    Image {
        id: petImage
        objectName: "companionImage"
        x: 16
        y: 16
        width: parent.width - 32
        height: width
        source: petWindow.companion.state.image
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        smooth: true
    }
    Rectangle {
        x: 8
        y: petWindow.companion.state.size
        width: parent.width - 16
        height: 88
        color: "#fff9e9"
        border.color: "#806d4b"
        Text {
            anchors.fill: parent
            anchors.margins: 8
            text: petWindow.companion.state.status + (petWindow.companion.state.phrase ? "\n" + petWindow.companion.state.phrase : "")
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: "#302d26"
            font.pixelSize: 12
            verticalAlignment: Text.AlignVCenter
        }
    }
}
