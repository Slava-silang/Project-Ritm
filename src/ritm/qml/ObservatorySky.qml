pragma ComponentBehavior: Bound
import QtQuick

Item {
    id: sky
    objectName: "observatorySky"
    required property var appearance
    required property bool moving
    required property real pointerX
    required property real pointerY
    clip: true

    Image {
        width: sky.width
        height: width / 3
        source: "observatory/sky.jpg"
        Rectangle {
            anchors.fill: parent
            gradient: Gradient {
                GradientStop { position: 0; color: "#18090e20" }
                GradientStop { position: 0.55; color: "#65090e20" }
                GradientStop { position: 1; color: sky.appearance.colors.background }
            }
        }
    }

    Repeater {
        model: 54
        delegate: Rectangle {
            id: star
            required property int index
            readonly property real restX: ((index * 137 + index * index * 7) % 997) / 997 * sky.width
            readonly property real restY: ((index * 181 + 37) % 569) / 569 * sky.height
            readonly property real dx: restX - sky.pointerX
            readonly property real dy: restY - sky.pointerY
            readonly property real distance: Math.sqrt(dx * dx + dy * dy)
            readonly property real push: sky.moving ? Math.max(0, 1 - distance / 100) * 10 : 0
            x: restX
            y: restY
            width: sky.appearance.dp(index % 5 === 0 ? 2 : 1.4)
            height: width
            radius: width / 2
            opacity: 0.35 + (index % 4) / 8
            color: index % 3 === 0 ? "#c0baff" : "#d5eeff"
            transform: Translate {
                x: star.dx / Math.max(1, star.distance) * star.push
                y: star.dy / Math.max(1, star.distance) * star.push
                Behavior on x { enabled: sky.moving; NumberAnimation { duration: 180 } }
                Behavior on y { enabled: sky.moving; NumberAnimation { duration: 180 } }
            }
        }
    }

    Item {
        id: flyby
        objectName: "observatoryFlyby"
        property bool saucer: false
        property real progress: 0
        visible: flight.running
        opacity: Math.min(1, progress * 8, (1 - progress) * 8)
        x: sky.width * (saucer ? 0.48 + progress * 0.27 : 0.81 - progress * 0.16)
        y: sky.appearance.dp(17 + (saucer ? Math.sin(progress * 5) * 5 : progress * 34))
        width: sky.appearance.dp(saucer ? 25 : 34)
        height: sky.appearance.dp(saucer ? 12 : 2)
        Canvas {
            anchors.fill: parent
            visible: flyby.saucer
            onPaint: {
                const ctx = getContext("2d");
                ctx.reset();
                ctx.scale(width / 25, height / 12);
                ctx.fillStyle = "#ada3ed";
                ctx.beginPath(); ctx.ellipse(8, 1, 9, 7); ctx.fill();
                ctx.fillStyle = "#706da7";
                ctx.beginPath(); ctx.ellipse(1, 5, 23, 6); ctx.fill();
                ctx.fillStyle = "#9de3f3";
                ctx.fillRect(5, 8, 3, 1); ctx.fillRect(11, 9, 3, 1); ctx.fillRect(17, 8, 3, 1);
            }
        }
        Rectangle {
            anchors.fill: parent
            visible: !flyby.saucer
            rotation: -17
            radius: height / 2
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: "#e6f5ff" }
                GradientStop { position: 1; color: "#00999aff" }
            }
        }
    }
    NumberAnimation {
        id: flight
        target: flyby
        property: "progress"
        from: 0; to: 1
        duration: flyby.saucer ? 4300 : 750
    }
    Timer {
        objectName: "observatoryFlightTimer"
        property int passes: 0
        interval: passes === 0 ? 8500 : 18000 + (passes % 3) * 4500
        running: sky.moving
        repeat: true
        onTriggered: {
            flyby.saucer = passes % 3 === 1;
            passes++;
            flight.restart();
        }
    }
    onMovingChanged: if (!moving) flight.stop()
}
