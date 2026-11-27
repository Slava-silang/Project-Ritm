pragma ComponentBehavior: Bound
import QtQuick

Item {
    id: glass
    objectName: "jungleRain"
    required property bool moving
    readonly property var locations: [
        Qt.point(0.83, 0.25), Qt.point(0.35, 0.13), Qt.point(0.68, 0.48),
        Qt.point(0.14, 0.36), Qt.point(0.91, 0.61), Qt.point(0.47, 0.29)
    ]
    property int nextLocation: 0
    property point origin: locations[0]
    property real phase: 0
    readonly property real slide: Math.max(0, (phase - 0.24) / 0.76)
    clip: true

    Canvas {
        id: drop
        objectName: "jungleRainDrop"
        visible: glide.running
        width: 19
        height: 55
        x: glass.origin.x * glass.width - width / 2 + 5 * glass.slide
        y: glass.origin.y * glass.height - height + Math.min(150, glass.height * 0.25)
            * glass.slide * glass.slide
        opacity: Math.min(0.82, glass.phase * 6, (1 - glass.phase) * 5)
        readonly property real stretch: 1 + 0.68 * glass.slide
        onStretchChanged: requestPaint()
        onVisibleChanged: if (visible) requestPaint()
        onPaint: {
            const ctx = getContext("2d");
            const x = width / 2;
            const feet = height - 3;
            const crown = feet - 10 * stretch;
            ctx.clearRect(0, 0, width, height);
            if (glass.slide > 0) {
                const trail = ctx.createLinearGradient(x, 0, x, crown);
                trail.addColorStop(0, "#00d2ebce");
                trail.addColorStop(1, "#49d2ebce");
                ctx.fillStyle = trail;
                ctx.beginPath();
                ctx.moveTo(x - 0.3, crown - 24 * glass.slide);
                ctx.quadraticCurveTo(x + 0.5, crown - 7, x + 1.6, crown + 3);
                ctx.lineTo(x - 1.2, crown + 3);
                ctx.closePath();
                ctx.fill();
            }
            const refraction = ctx.createLinearGradient(x - 5, crown, x + 4, feet);
            refraction.addColorStop(0, "#a9f1ffe1");
            refraction.addColorStop(0.24, "#362a443b");
            refraction.addColorStop(0.67, "#2810231e");
            refraction.addColorStop(1, "#c3cbeada");
            ctx.fillStyle = refraction;
            ctx.strokeStyle = "#6390baa7";
            ctx.lineWidth = 0.7;
            ctx.beginPath();
            ctx.moveTo(x + 0.4, crown);
            ctx.bezierCurveTo(x + 3.8, crown - 0.3, x + 6.2, feet - 4, x + 3.2, feet - 1);
            ctx.bezierCurveTo(x + 1, feet + 1.5, x - 4.9, feet + 0.8, x - 5, feet - 3.1);
            ctx.bezierCurveTo(x - 5.6, feet - 6.3, x - 2, crown + 0.6, x + 0.4, crown);
            ctx.closePath();
            ctx.fill();
            ctx.stroke();
        }
    }

    NumberAnimation {
        id: glide
        target: glass
        property: "phase"
        from: 0
        to: 1
        duration: 1050
    }
    Timer {
        objectName: "jungleRainTimer"
        interval: 5000
        repeat: true
        running: glass.moving
        onTriggered: {
            glass.origin = glass.locations[glass.nextLocation];
            glass.nextLocation = (glass.nextLocation + 1) % glass.locations.length;
            glide.restart();
        }
        onRunningChanged: if (!running) glide.stop()
    }
}
