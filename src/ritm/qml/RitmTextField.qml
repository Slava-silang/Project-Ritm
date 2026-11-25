import QtQuick
import QtQuick.Controls

TextField {
    id: field
    required property var appearance
    property bool invalid: false

    background: RitmControlBackground {
        appearance: field.appearance
        implicitWidth: appearance.usesSharedStyle ? appearance.dp(130) : 200
        implicitHeight: appearance.usesSharedStyle ? appearance.dp(32) : 40
        kind: "field"
        focused: field.activeFocus
        available: field.enabled
        legacyColor: field.palette.base
        border.color: field.invalid ? field.appearance.colors.warning
            : field.appearance.graphite ? (field.activeFocus ? field.appearance.colors.accent
                : field.enabled ? field.appearance.colors.controlBorder : field.appearance.colors.border)
            : legacyBorder
        legacyBorder: field.activeFocus ? field.palette.highlight
            : Application.styleHints.accessibility.contrastPreference !== Qt.HighContrast ? field.palette.mid
            : field.enabled ? field.palette.text : Qt.tint(field.palette.base,
                Qt.rgba(field.palette.text.r, field.palette.text.g, field.palette.text.b, 0.5))
        legacyBorderWidth: field.activeFocus ? 2 : 1
    }
}
