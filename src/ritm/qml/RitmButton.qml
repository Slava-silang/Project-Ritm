import QtQuick
import QtQuick.Controls

Button {
    id: button
    required property var appearance
    property bool primary: false
    property bool quiet: false
    property bool compact: false
    readonly property color legacyFill: checked || highlighted ? palette.dark : palette.button
    readonly property color primaryText: appearance.jungle && enabled ? appearance.colors.text : appearance.colors.onAccent

    highlighted: primary
    // The Basic style reads brightText for checked and highlighted buttons.
    palette.brightText: appearance.usesSharedStyle && highlighted
        ? (primary ? primaryText : appearance.colors.accent) : appearance.colors.text
    palette.buttonText: appearance.usesSharedStyle && primary ? primaryText : appearance.colors.text

    background: RitmControlBackground {
        appearance: button.appearance
        implicitWidth: appearance.usesSharedStyle ? 0 : button.compact ? 40 : 100
        implicitHeight: appearance.usesSharedStyle ? appearance.dp(30) : 40
        visible: appearance.usesSharedStyle || !button.flat || button.down || button.checked || button.highlighted
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
