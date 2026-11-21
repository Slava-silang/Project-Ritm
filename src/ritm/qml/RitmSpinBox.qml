pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls

SpinBox {
    id: control
    required property var appearance

    component StepIndicator: Item {
        id: step
        required property bool increase
        readonly property bool pressed: increase ? control.up.pressed : control.down.pressed
        implicitWidth: control.appearance.graphite ? control.appearance.dp(34) : 40
        implicitHeight: control.appearance.graphite ? control.appearance.dp(34) : 40
        x: increase !== control.mirrored ? control.width - width : 0
        height: control.height

        Rectangle {
            anchors.fill: parent
            anchors.margins: control.appearance.graphite ? control.appearance.dp(3) : 0
            radius: control.appearance.graphite ? control.appearance.dp(8) : 0
            color: step.pressed ? control.palette.mid : control.palette.button
            Label {
                anchors.centerIn: parent
                text: step.increase ? "+" : "−"
                color: step.enabled ? control.palette.buttonText : control.palette.mid
                font: control.font
            }
        }
    }

    up.indicator: StepIndicator { increase: true }
    down.indicator: StepIndicator { increase: false }
    background: GraphiteBackground {
        appearance: control.appearance
        kind: "field"
        implicitWidth: control.appearance.graphite ? control.appearance.dp(112) : 140
        focused: control.activeFocus
        available: control.enabled
        legacyColor: control.enabled ? control.palette.base : control.palette.button
        legacyBorder: control.palette.button
        legacyBorderWidth: 1
    }
}
