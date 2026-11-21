import QtQuick
import QtQuick.Controls

TextField {
    id: field
    required property var appearance

    background: GraphiteBackground {
        appearance: field.appearance
        implicitWidth: appearance.graphite ? appearance.dp(130) : 200
        implicitHeight: appearance.graphite ? appearance.dp(32) : 40
        kind: "field"
        focused: field.activeFocus
        available: field.enabled
        legacyColor: field.palette.base
        legacyBorder: field.activeFocus ? field.palette.highlight
            : Application.styleHints.accessibility.contrastPreference !== Qt.HighContrast ? field.palette.mid
            : field.enabled ? field.palette.text : Qt.tint(field.palette.base,
                Qt.rgba(field.palette.text.r, field.palette.text.g, field.palette.text.b, 0.5))
        legacyBorderWidth: field.activeFocus ? 2 : 1
    }
}
