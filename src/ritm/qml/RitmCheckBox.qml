pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls

CheckBox {
    id: control
    required property var appearance

    indicator: Rectangle {
        id: box
        implicitWidth: control.appearance.usesSharedStyle ? control.appearance.dp(20) : 28
        implicitHeight: implicitWidth
        radius: control.appearance.usesSharedStyle ? control.appearance.dp(5) : 0
        x: control.text ? (control.mirrored ? control.width - width - control.rightPadding : control.leftPadding)
            : control.leftPadding + (control.availableWidth - width) / 2
        y: control.topPadding + (control.availableHeight - height) / 2
        opacity: control.enabled ? 1 : 0.65
        color: !control.appearance.usesSharedStyle ? (control.down ? control.palette.light : control.palette.base)
            : control.checkState !== Qt.Unchecked ? control.appearance.colors.accentFill
            : control.down ? control.appearance.colors.raised : control.appearance.colors.field
        border.width: control.visualFocus ? 2 : 1
        border.color: !control.appearance.usesSharedStyle
            ? (control.visualFocus ? control.palette.highlight : control.palette.mid)
            : control.visualFocus
                ? (control.checkState === Qt.Unchecked ? control.appearance.colors.accent : control.appearance.colors.onAccent)
            : control.checkState !== Qt.Unchecked ? control.appearance.colors.accentFill : control.appearance.colors.controlBorder

        Canvas {
            id: checkMark
            anchors.centerIn: parent
            width: box.width * 0.68
            height: width
            property int mark: control.checkState
            property color ink: control.appearance.usesSharedStyle ? control.appearance.colors.onAccent : control.palette.text
            onMarkChanged: requestPaint()
            onInkChanged: requestPaint()
            onPaint: {
                const ctx = getContext("2d");
                ctx.reset();
                ctx.scale(width / 16, height / 16);
                ctx.strokeStyle = ink;
                ctx.lineWidth = 2;
                ctx.lineCap = "round";
                ctx.lineJoin = "round";
                ctx.beginPath();
                if (mark === Qt.Checked) {
                    ctx.moveTo(2, 8);
                    ctx.lineTo(6, 12);
                    ctx.lineTo(14, 4);
                } else if (mark === Qt.PartiallyChecked) {
                    ctx.moveTo(3, 8);
                    ctx.lineTo(13, 8);
                }
                ctx.stroke();
            }
        }
    }
}
