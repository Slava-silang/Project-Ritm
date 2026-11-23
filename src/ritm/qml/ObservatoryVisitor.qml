import QtQuick

Item {
    id: visitor
    objectName: "observatoryVisitor"
    required property bool reduceMotion
    property bool tucked: !reduceMotion
    clip: true
    Component.onCompleted: {
        if (tucked)
            returnTimer.restart();
    }

    Image {
        anchors.fill: parent
        source: "observatory/visitor.png"
        fillMode: Image.PreserveAspectFit
        transform: Translate {
            y: visitor.tucked ? visitor.height : 0
            Behavior on y {
                enabled: !visitor.reduceMotion
                NumberAnimation { duration: visitor.tucked ? 150 : 300; easing.type: Easing.OutCubic }
            }
        }
    }
    Rectangle {
        anchors.bottom: parent.bottom
        width: parent.width
        height: 1
        color: "#a89fda"
    }
    Timer {
        id: returnTimer
        interval: 950
        onTriggered: visitor.tucked = false
    }
    TapHandler {
        cursorShape: Qt.PointingHandCursor
        onTapped: {
            visitor.tucked = true;
            returnTimer.restart();
        }
    }
}
