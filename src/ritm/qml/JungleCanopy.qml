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

    Canvas {
        id: stem
        objectName: "jungleLeafStem"
        visible: frog.visible
        x: frog.x + frog.width * 0.90
        width: canopy.appearance.dp(canopy.compactWildlife ? 47 : 85)
        height: frog.y + frog.height * 0.50
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            const ctx = getContext("2d");
            ctx.clearRect(0, 0, width, height);
            const green = ctx.createLinearGradient(0, height, width, 0);
            green.addColorStop(0, "#879941");
            green.addColorStop(0.42, "#52622d");
            green.addColorStop(1, "#283a19");
            ctx.fillStyle = green;
            ctx.beginPath();
            ctx.moveTo(0, height - 4);
            ctx.bezierCurveTo(width * 0.44, height - 10, width * 0.68, height * 0.64, width * 0.84, 0);
            ctx.lineTo(width * 0.94, 0);
            ctx.bezierCurveTo(width * 0.76, height * 0.75, width * 0.47, height - 5, 0, height);
            ctx.closePath();
            ctx.fill();
            ctx.strokeStyle = "#9aa759";
            ctx.lineWidth = 0.9;
            ctx.beginPath();
            ctx.moveTo(0, height - 3);
            ctx.bezierCurveTo(width * 0.44, height - 9, width * 0.68, height * 0.66, width * 0.87, 0);
            ctx.stroke();
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
        readonly property real mouthY: y + height * 0.20
    }

    Image {
        id: insect
        objectName: "jungleInsect"
        property bool butterfly: false
        property bool willCatch: true
        property real phase: 0
        readonly property real impact: 0.76
        readonly property real approach: willCatch ? Math.min(1, phase / impact) : Math.sin(phase * Math.PI)
        readonly property real travel: canopy.appearance.dp(canopy.compactWildlife ? 59 : 108)
        readonly property real catchX: frog.x - canopy.appearance.dp(canopy.compactWildlife ? 38 : 64)
        readonly property real catchY: frog.mouthY + canopy.appearance.dp(12)
        readonly property real caught: willCatch ? Math.max(0, Math.min(1, (phase - impact) / 0.047)) : 0
        visible: flight.running && caught < 1
        x: catchX - travel * (1 - approach) + canopy.appearance.dp(10) * caught - width / 2
        y: catchY - height / 2 + (1 - approach) * canopy.appearance.dp(13) * Math.sin(phase * 17)
        width: canopy.appearance.dp(butterfly ? 22 : 30)
        height: width
        fillMode: Image.PreserveAspectFit
        source: butterfly ? "jungle/butterfly.png" : "jungle/dragonfly.png"
        mirror: !butterfly && (willCatch || phase < 0.5)
        mipmap: true
        rotation: Math.sin(phase * 16) * 7
        opacity: (1 - caught) * Math.min(1, phase * 8, (1 - phase) * 7)
        transform: Scale {
            origin.x: insect.width / 2
            xScale: 1 - (insect.butterfly ? 0.30 : 0.12) * Math.abs(Math.sin(insect.phase * 83))
        }
    }

    Canvas {
        id: tongue
        objectName: "jungleTongue"
        readonly property real offset: insect.phase - insect.impact
        readonly property real reach: offset < 0 ? Math.max(0, 1 + offset / 0.056)
            : Math.pow(Math.max(0, 1 - offset / 0.095), 1.35)
        visible: flight.running && insect.willCatch && reach > 0.045
        x: insect.catchX - canopy.appearance.dp(5)
        y: frog.mouthY - canopy.appearance.dp(4)
        width: frog.mouthX - x + canopy.appearance.dp(4)
        height: canopy.appearance.dp(24)
        onReachChanged: requestPaint()
        onVisibleChanged: if (visible) requestPaint()
        onPaint: {
            const ctx = getContext("2d");
            const dp = canopy.appearance.dp(1);
            const rootX = width - 4 * dp;
            const rootY = 4 * dp;
            const tipX = rootX - (rootX - 5 * dp) * reach;
            const tipY = rootY + 12 * dp * reach;
            ctx.clearRect(0, 0, width, height);
            if (reach <= 0)
                return;
            const color = ctx.createLinearGradient(rootX, rootY, tipX, tipY);
            color.addColorStop(0, "#712e30");
            color.addColorStop(0.27, "#d16b61");
            color.addColorStop(0.8, "#ec9983");
            color.addColorStop(1, "#faad93");
            ctx.fillStyle = color;
            ctx.strokeStyle = "#74322f";
            ctx.lineWidth = dp * 0.65;
            ctx.beginPath();
            ctx.moveTo(rootX, rootY - 1.4 * dp);
            ctx.bezierCurveTo(rootX - 14 * dp * reach, rootY - 4 * dp,
                tipX + 18 * dp * reach, tipY - 2 * dp, tipX + 2 * dp, tipY - 2.1 * dp);
            ctx.quadraticCurveTo(tipX - 4 * dp, tipY - 1.2 * dp, tipX - 2.1 * dp, tipY + 2 * dp);
            ctx.bezierCurveTo(tipX + 8 * dp, tipY + 3.3 * dp,
                rootX - 15 * dp * reach, rootY + 3 * dp, rootX, rootY + 1.4 * dp);
            ctx.closePath();
            ctx.fill();
            ctx.stroke();
            ctx.strokeStyle = "#bbf9c8b5";
            ctx.lineWidth = dp * 0.7;
            ctx.beginPath();
            ctx.moveTo(rootX - 2 * dp, rootY - 0.6 * dp);
            ctx.quadraticCurveTo(rootX - 19 * dp * reach, rootY - 1.5 * dp, tipX + 3 * dp, tipY - 0.9 * dp);
            ctx.stroke();
        }
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
