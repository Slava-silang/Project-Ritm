import QtQuick
import QtQuick.Controls

Button {
    id: button
    required property var appearance
    property bool primary: false
    property bool quiet: false
    property bool compact: false
    readonly property color legacyFill: checked || highlighted ? palette.dark : palette.button

    highlighted: primary
    // The Basic style reads brightText for checked and highlighted buttons.
    palette.brightText: appearance.graphite && highlighted
        ? (primary ? appearance.colors.onAccent : appearance.colors.accent) : appearance.colors.text
    palette.buttonText: appearance.graphite && primary ? appearance.colors.onAccent : appearance.colors.text

    background: GraphiteBackground {
        appearance: button.appearance
        implicitWidth: appearance.graphite ? 0 : button.compact ? 40 : 100
        implicitHeight: appearance.graphite ? appearance.dp(30) : 40
        visible: appearance.graphite || !button.flat || button.down || button.checked || button.highlighted
        primary: button.primary
        quiet: button.quiet
        selected: button.highlighted
        hovered: button.hovered
        pressed: button.down
        focused: button.visualFocus
        available: button.enabled
        legacyColor: button.down ? Qt.tint(button.legacyFill,
            Qt.rgba(button.palette.mid.r, button.palette.mid.g, button.palette.mid.b, 0.5)) : button.legacyFill
        legacyBorder: button.visualFocus ? button.palette.highlight : button.palette.windowText
        legacyBorderWidth: button.visualFocus ? 2 : Application.styleHints.accessibility.contrastPreference === Qt.HighContrast ? 1 : 0
    }
}
