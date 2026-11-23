import QtQuick
import QtQuick.Controls

TextArea {
    id: input
    required property var appearance

    background: RitmControlBackground {
        appearance: input.appearance
        kind: "field"
        visible: (appearance.usesSharedStyle && !input.readOnly)
            || Application.styleHints.accessibility.contrastPreference === Qt.HighContrast
        focused: input.activeFocus
        available: input.enabled
        legacyBorderWidth: input.activeFocus ? 2 : 1
        legacyBorder: input.activeFocus ? input.palette.highlight
            : input.enabled ? input.palette.mid : input.palette.midlight
    }
}
