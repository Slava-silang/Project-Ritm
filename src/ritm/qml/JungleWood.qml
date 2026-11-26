pragma ComponentBehavior: Bound
import QtQuick

Item {
    id: wood
    required property real uiScale
    required property bool hovered
    required property bool pressed

    BorderImage {
        objectName: "jungleWoodTexture"
        width: Math.max(0, wood.width / wood.uiScale - 4)
        height: Math.max(0, wood.height / wood.uiScale - 4)
        anchors.centerIn: parent
        scale: wood.uiScale
        source: "jungle/wood.png"
        border { left: 10; right: 10; top: 10; bottom: 10 }
        opacity: wood.pressed ? 0.65 : wood.hovered ? 0.85 : 1
        smooth: true
    }
}
