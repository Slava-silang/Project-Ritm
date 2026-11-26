pragma ComponentBehavior: Bound
import QtQuick

Item {
    id: canopy
    objectName: "jungleCanopy"
    required property var appearance
    required property bool moving
    readonly property bool compactWildlife: width < appearance.dp(660)
    readonly property bool wildlifeVisible: width >= appearance.dp(515)
    clip: true

    Image {
        anchors.top: parent.top
        width: parent.width
        height: Math.min(parent.height, Math.max(width / 3, canopy.appearance.dp(210)))
        source: "jungle/forest.jpg"
        fillMode: Image.PreserveAspectCrop
        Rectangle {
            anchors.fill: parent
            gradient: Gradient {
                GradientStop { position: 0; color: "#68101c13" }
                GradientStop { position: 0.38; color: "#70101c13" }
                GradientStop { position: 1; color: canopy.appearance.colors.background }
            }
        }
    }

    Image {
        id: frog
        objectName: "jungleFrog"
        visible: canopy.wildlifeVisible
        anchors.top: parent.top
        anchors.topMargin: canopy.appearance.dp(canopy.compactWildlife ? 3 : 16)
        anchors.right: parent.right
        anchors.rightMargin: canopy.appearance.dp(canopy.compactWildlife ? 126 : 147)
        width: canopy.appearance.dp(canopy.compactWildlife ? 68 : 110)
        height: width * 0.70
        source: "jungle/frog.png"
        mipmap: true
        readonly property real mouthX: x + width * 0.11
        readonly property real mouthY: y + height * 0.22
    }

    Image {
        id: insect
        objectName: "jungleInsect"
        property bool butterfly: false
        property bool willCatch: true
        property real phase: 0
        readonly property real impact: 0.76
        readonly property real approach: Math.min(1, phase / impact)
        readonly property real distance: canopy.appearance.dp(canopy.compactWildlife ? 86 : 156)
        readonly property real catchDistance: canopy.appearance.dp(28)
        visible: flight.running && (!willCatch || phase < impact)
        x: frog.mouthX - distance + (distance - catchDistance) * (willCatch ? approach : phase)
            - width / 2 + (willCatch ? 0 : canopy.appearance.dp(60) * phase)
        y: frog.mouthY - height / 2 + (willCatch ? (1 - approach) : 1)
            * canopy.appearance.dp(11) * Math.sin(phase * 16)
        width: canopy.appearance.dp(butterfly ? 22 : 30)
        height: width
        fillMode: Image.PreserveAspectFit
        source: butterfly ? "jungle/butterfly.png" : "jungle/dragonfly.png"
        mirror: !butterfly
        mipmap: true
        rotation: Math.sin(phase * 16) * 7
        opacity: Math.min(1, phase * 8, (1 - phase) * 7)
        transform: Scale {
            origin.x: insect.width / 2
            xScale: 1 - (insect.butterfly ? 0.30 : 0.12) * Math.abs(Math.sin(insect.phase * 83))
        }
    }

    Rectangle {
        id: tongue
        objectName: "jungleTongue"
        readonly property real reach: Math.max(0, 1 - Math.abs(insect.phase - insect.impact) / 0.067)
        visible: flight.running && insect.willCatch && reach > 0
        x: frog.mouthX - width
        y: frog.mouthY - height / 2
        width: (insect.catchDistance + canopy.appearance.dp(2)) * reach
        height: canopy.appearance.dp(3)
        radius: height / 2
        color: "#f39287"
        antialiasing: true
    }

    NumberAnimation {
        id: flight
        target: insect
        property: "phase"
        from: 0
        to: 1
        duration: 2050
    }
    Timer {
        objectName: "jungleFlightTimer"
        property int flights: 0
        interval: flights === 0 ? 5200 : 12000 + (flights % 3) * 2200
        repeat: true
        running: canopy.moving && canopy.wildlifeVisible
        onTriggered: {
            insect.butterfly = flights % 2 === 1;
            insect.willCatch = flights % 3 !== 2;
            flights++;
            flight.restart();
        }
        onRunningChanged: if (!running) flight.stop()
    }
}
