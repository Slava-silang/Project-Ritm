import QtQuick
import QtQuick.Controls
import QtQuick.Templates as Templates

ComboBox {
    id: box
    required property var appearance
    palette.dark: appearance.graphite ? appearance.colors.muted : appearance.colors.border
    palette.base: appearance.graphite ? appearance.colors.field : appearance.colors.surface
    palette.button: appearance.graphite ? appearance.colors.field : appearance.colors.raised

    background: GraphiteBackground {
        appearance: box.appearance
        implicitWidth: appearance.graphite ? appearance.dp(120) : 140
        implicitHeight: appearance.graphite ? appearance.dp(32) : 40
        kind: "field"
        focused: box.activeFocus
        available: box.enabled
        visible: appearance.graphite || !box.flat || box.down
        legacyColor: box.down ? box.palette.mid : box.palette.button
        legacyBorder: !box.editable && box.visualFocus ? box.palette.highlight : box.palette.buttonText
        legacyBorderWidth: !box.editable && box.visualFocus ? 2
            : Application.styleHints.accessibility.contrastPreference === Qt.HighContrast ? 1 : 0
    }
    Binding {
        when: box.appearance.graphite && box.editable
        target: (box.contentItem as Templates.TextField)?.background ?? null
        property: "visible"
        value: false
    }
}
