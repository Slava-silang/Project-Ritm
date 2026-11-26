import QtQuick

Item {
    id: visitor
    required property bool reduceMotion
    required property url portrait
    required property color ledgeColor
    property bool tucked: !reduceMotion
    clip: true
    Component.onCompleted: {
        if (tucked)
            returnTimer.restart();
    }

    Image {
        anchors.fill: parent
        source: visitor.portrait
        fillMode: Image.PreserveAspectFit
        mipmap: true
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
        color: visitor.ledgeColor
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
