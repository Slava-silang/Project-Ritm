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
        property string route: "rightMeteor"
        readonly property bool saucer: route === "saucer"
        readonly property bool leftMeteor: route === "leftMeteor"
        readonly property real startX: leftMeteor ? Math.min(sky.width * 0.34, sky.appearance.dp(315))
            : sky.width * (saucer ? 0.54 : 0.81)
        readonly property real endX: leftMeteor ? Math.min(sky.width * 0.055, sky.appearance.dp(42))
            : sky.width * (saucer ? 0.79 : 0.65)
        readonly property real startY: sky.appearance.dp(saucer ? 68 : leftMeteor ? 10 : 17)
        readonly property real endY: sky.appearance.dp(saucer ? 5 : leftMeteor ? 74 : 51)
        readonly property real angle: Math.atan2(endY - startY, endX - startX) * 180 / Math.PI
        property real progress: 0
        visible: flight.running
        opacity: Math.min(1, progress * 9, (1 - progress) * (saucer ? 2.6 : 5))
        x: startX + (endX - startX) * progress
        y: startY + (endY - startY) * progress
        Image {
            objectName: "observatorySaucer"
            anchors.centerIn: parent
            visible: flyby.saucer
            source: "observatory/saucer.png"
            width: sky.appearance.dp(40)
            height: sky.appearance.dp(20)
            fillMode: Image.PreserveAspectFit
            smooth: true
            mipmap: true
            rotation: flyby.angle
            scale: 1.15 - flyby.progress * 0.95
        }
        Rectangle {
            x: -sky.appearance.dp(1.5)
            y: -height / 2
            width: sky.appearance.dp(flyby.leftMeteor ? 39 : 34)
            height: sky.appearance.dp(flyby.leftMeteor ? 3 : 2)
            visible: !flyby.saucer
            transformOrigin: Item.Left
            rotation: flyby.angle - 180
            antialiasing: true
            radius: height / 2
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: "#f2f8ff" }
                GradientStop { position: 0.2; color: "#b4dfff" }
                GradientStop { position: 1; color: "#00999aff" }
            }
        }
    }
    NumberAnimation {
        id: flight
        target: flyby
        property: "progress"
        from: 0; to: 1
        duration: flyby.saucer ? 1550 : 750
        easing.type: flyby.saucer ? Easing.InQuad : Easing.Linear
    }
    Timer {
        objectName: "observatoryFlightTimer"
        readonly property var routes: ["rightMeteor", "leftMeteor", "saucer"]
        property int passes: 0
        interval: passes === 0 ? 8500 : 14000 + (passes % routes.length) * 2500
        running: sky.moving
        repeat: true
        onTriggered: {
            flyby.route = routes[passes % routes.length];
            passes++;
            flight.restart();
        }
    }
    onMovingChanged: if (!moving) flight.stop()
}
