import QtQuick

Rectangle {
    id: style
    required property var appearance
    property string kind: "button"
    property bool primary: false
    property bool quiet: false
    property bool selected: false
    property bool hovered: false
    property bool pressed: false
    property bool focused: false
    property bool available: true
    property color legacyColor: "transparent"
    property color legacyBorder: "transparent"
    property int legacyBorderWidth: 0
    readonly property bool isField: kind === "field"
    readonly property bool isDialog: kind === "dialog"
    readonly property var colors: appearance.colors

    radius: style.isDialog ? style.appearance.dialogRadius(0) : style.appearance.controlRadius(0)
    color: {
        if (!style.appearance.usesSharedStyle)
            return style.legacyColor;
        if (style.isDialog)
            return style.colors.surface;
        if (style.isField)
            return style.colors.field;
        if (style.primary)
            return !style.available ? style.colors.disabled : style.pressed
                ? style.colors.accentDown : style.hovered ? style.colors.accentHover : style.colors.accentFill;
        if (style.selected || style.pressed)
            return style.colors.selection;
        return style.hovered && style.available ? style.colors.raised
            : style.quiet ? "transparent" : style.colors.surface;
    }
    border.width: !style.appearance.usesSharedStyle ? style.legacyBorderWidth
        : style.focused ? 2 : style.primary || style.selected || style.quiet ? 0 : 1
    border.color: !style.appearance.usesSharedStyle ? style.legacyBorder : style.focused
        ? style.colors.accent : style.isDialog || !style.available ? style.colors.border : style.colors.controlBorder
    Behavior on color {
        enabled: style.appearance.usesSharedStyle && !style.isDialog
        ColorAnimation { duration: 110 }
    }
}
