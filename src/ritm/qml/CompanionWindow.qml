import QtQuick

Window {
    id: petWindow
    required property var companion
    title: qsTr("Ритм — компаньон")
    objectName: "companionWindow"
    visible: false
    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
    width: companion.state.size
    height: width
    Image {
        objectName: "companionImage"
        anchors.fill: parent
        anchors.margins: 16
        source: petWindow.companion.state.image
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        smooth: true
    }
}
